"""
IVA converter: upload a PDF (or screenshot) -> one HTML package per page.
Each package: index.html, css/style.css, images/background.jpg  (1024x768)

Install:
  pip install fastapi uvicorn python-multipart pymupdf opencv-python-headless pytesseract pillow numpy
  (and install the Tesseract OCR binary: apt install tesseract-ocr / brew install tesseract)
Run:
  uvicorn app:app --reload      then open http://127.0.0.1:8000
"""
import html, io, zipfile
from collections import defaultdict

import cv2, fitz, numpy as np, pytesseract
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, Response
from PIL import Image

W, H, S = 1024, 768, 2            # target size, OCR scale factor
FONT = "'Oswald','Arial Narrow',sans-serif"
IMG_EXT, IMG_FMT = "jpg", "JPEG"  # use "png"/"PNG" for lossless

app = FastAPI()


# ---------- 1. input -> list of PIL pages ----------
def load_pages(data: bytes, name: str):
    if name.lower().endswith(".pdf"):
        for p in fitz.open(stream=data, filetype="pdf"):
            yield Image.open(io.BytesIO(p.get_pixmap(dpi=200).tobytes("png"))).convert("RGB")
    else:
        yield Image.open(io.BytesIO(data)).convert("RGB")


# ---------- 2. OCR -> text lines with boxes (in 1024x768 units) ----------
def ocr_lines(rgb):
    d = pytesseract.image_to_data(rgb, output_type=pytesseract.Output.DICT, config="--psm 11")
    groups = defaultdict(list)
    for i, t in enumerate(d["text"]):
        if t.strip() and float(d["conf"][i]) > 50:
            groups[(d["block_num"][i], d["par_num"][i], d["line_num"][i])].append(i)
    lines = []
    for idx in groups.values():
        idx.sort(key=lambda i: d["left"][i])
        x0 = min(d["left"][i] for i in idx); y0 = min(d["top"][i] for i in idx)
        x1 = max(d["left"][i] + d["width"][i] for i in idx)
        y1 = max(d["top"][i] + d["height"][i] for i in idx)
        lines.append({"text": " ".join(d["text"][i] for i in idx), "box": (x0, y0, x1, y1)})
    return lines


# ---------- 3. colours + erase text from the image ----------
def analyse(rgb_arr, box, pad=4):
    x0, y0, x1, y1 = box
    x0, y0, x1, y1 = max(x0 - pad, 0), max(y0 - pad, 0), x1 + pad, y1 + pad
    crop = rgb_arr[y0:y1, x0:x1].reshape(-1, 3).astype(int)
    q = (crop // 16) * 16
    vals, cnt = np.unique(q, axis=0, return_counts=True)
    bg = np.median(crop[(q == vals[cnt.argmax()]).all(1)], axis=0)
    dist = np.linalg.norm(crop - bg, axis=1)
    fg = np.median(crop[dist >= np.percentile(dist, 95)], axis=0)
    return (x0, y0, x1, y1), bg, fg


def erase_text(rgb_arr, lines):
    mask = np.zeros(rgb_arr.shape[:2], np.uint8)
    for l in lines:
        (x0, y0, x1, y1), bg, fg = analyse(rgb_arr, l["box"])
        l["fg"] = "#%02x%02x%02x" % tuple(int(v) for v in fg)
        region = rgb_arr[y0:y1, x0:x1].astype(int)
        mask[y0:y1, x0:x1] |= (np.linalg.norm(region - bg, axis=2) > 40).astype(np.uint8) * 255
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
    return cv2.inpaint(rgb_arr, mask, 3, cv2.INPAINT_TELEA)


# ---------- 4. build the package ----------
def build_package(page: Image.Image) -> bytes:
    big = np.array(page.resize((W * S, H * S), Image.LANCZOS))
    lines = ocr_lines(big)
    clean = erase_text(big, lines)

    bg_img = Image.fromarray(clean).resize((W, H), Image.LANCZOS)
    buf = io.BytesIO(); bg_img.save(buf, IMG_FMT, quality=90)

    items, rules = [], []
    for i, l in enumerate(lines):
        x0, y0, x1, y1 = [v / S for v in l["box"]]
        h = y1 - y0
        items.append(f'  <div class="t" id="t{i}" data-w="{x1 - x0:.1f}">{html.escape(l["text"])}</div>')
        rules.append(f'#t{i}{{left:{x0:.1f}px;top:{y0:.1f}px;height:{h:.1f}px;'
                     f'line-height:{h:.1f}px;font-size:{h:.1f}px;color:{l["fg"]}}}')

    index = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width={W}, height={H}, initial-scale=1, user-scalable=no">
<title>Slide</title><link rel="stylesheet" href="css/style.css"></head>
<body><div id="slide">
{chr(10).join(items)}
</div>
<script>
/* scale each text so its width matches the original screenshot */
document.fonts.ready.then(function(){{document.querySelectorAll('.t').forEach(function(e){{
  var w=parseFloat(e.dataset.w),cur=e.getBoundingClientRect().width;
  if(cur>0)e.style.fontSize=(parseFloat(getComputedStyle(e).fontSize)*w/cur)+'px';}});}});
</script></body></html>"""

    css = f"""@import url('https://fonts.googleapis.com/css2?family=Oswald:wght@400;500&display=swap');
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:100%;height:100%;background:#222}}
#slide{{position:relative;width:{W}px;height:{H}px;margin:0 auto;overflow:hidden;
  background:url('../images/background.{IMG_EXT}') no-repeat 0 0/{W}px {H}px;font-family:{FONT}}}
.t{{position:absolute;white-space:nowrap}}
{chr(10).join(rules)}
"""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("index.html", index)
        z.writestr("css/style.css", css)
        z.writestr(f"images/background.{IMG_EXT}", buf.getvalue())
    return out.getvalue()


# ---------- 5. API ----------
@app.post("/convert")
async def convert(file: UploadFile = File(...)):
    data = await file.read()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:       # outer zip = one zip per page
        for n, page in enumerate(load_pages(data, file.filename), 1):
            z.writestr(f"page_{n:02d}.zip", build_package(page))
    return Response(out.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="iva_packages.zip"'})


@app.get("/", response_class=HTMLResponse)
def form():
    return """<h2>PDF / screenshot to HTML packages</h2>
<form action="/convert" method="post" enctype="multipart/form-data">
<input type="file" name="file" accept=".pdf,image/*" required> <button>Convert</button></form>"""