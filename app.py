"""
IVA converter v2: PDF / image  ->  one semantic HTML package per page.
Package: index.html, css/style.css, images/background.jpg

* PDF pages with a text layer: exact text, fonts, sizes, colours and positions come from the PDF itself,
  and the text is removed from the background by redaction (no inpainting artefacts).
* Images / scanned PDF pages: Tesseract OCR + pixel colour sampling + OpenCV inpainting.
* Optional: Claude (vision) assigns semantic roles (h1/h2/p/button...), fixes OCR typos, suggests a font.
* Layout: recursive XY-cut -> nested flex rows/columns. Positions are margins + flex alignment,
  all in `cqw` units (1% of slide width), so the slide scales with its 4:3 / 16:9 / any aspect ratio.

Run:  uvicorn app:app --reload
"""
import base64, html, io, json, os, re, zipfile
from collections import Counter, defaultdict

import cv2, numpy as np, pytesseract
from PIL import Image

MAX_W = 2048                      # max width of exported background image
EPS = 0.15                        # cqw: smallest gap treated as a real gap when cutting layout
CENTER_TOL = 0.6                  # cqw: tolerance for "centred" / "right aligned" detection
FIT_TEXT = True                   # tiny JS that matches font-size to the measured text width
DEFAULT_FONT = "Roboto"           # used for OCR pages when Claude gives no font hint
IMG_EXT, IMG_FMT = "jpg", "JPEG"  # or "png", "PNG"
USE_CLAUDE = bool(os.getenv("ANTHROPIC_API_KEY"))
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")

SNAP = {(16, 9): 16 / 9, (4, 3): 4 / 3, (16, 10): 1.6, (3, 2): 1.5, (1, 1): 1, (9, 16): 9 / 16,
        (3, 4): 3 / 4, (2, 3): 2 / 3, (10, 16): .625, (297, 210): 297 / 210, (210, 297): 210 / 297}
SYSTEM_FONTS = {"Arial", "Helvetica", "Times New Roman", "Times", "Courier New", "Courier", "Georgia",
                "Verdana", "Tahoma", "Trebuchet MS", "Calibri", "Cambria", "Segoe UI", ""}
TAGS = {"h1": "h1", "h2": "h2", "h3": "h3", "p": "p", "span": "span", "label": "span", "button": "button"}


def snap(w, h):
    r = w / h
    for (a, b), v in SNAP.items():
        if abs(r / v - 1) < 0.015:
            return a, b
    return round(w), round(h)


# =====================================================================================
# 1. EXTRACTION  -> raw blocks (units = pixels or points of the source page)
#    block = {lines:[[(text,style)]], x0,y0,x1,y1 (CSS box), pitch, align, st (dominant style), role}
#    style = (family, generic, size, colour, bold, italic)
# =====================================================================================
def detect_align(boxes, tol):
    if len(boxes) < 2:
        return "left"
    x0s, x1s = [b[0] for b in boxes], [b[1] for b in boxes]
    cs = [(a + b) / 2 for a, b in boxes]
    if max(x0s) - min(x0s) <= tol: return "left"
    if max(cs) - min(cs) <= tol: return "center"
    if max(x1s) - min(x1s) <= tol: return "right"
    return "left"


def make_block(lines, line_boxes, st, pitch_default, tol):
    """lines: [[(text,style)]]; line_boxes: [(x0,y0,x1,y1)] ink/line boxes of each line."""
    n = len(lines)
    h1 = line_boxes[0][3] - line_boxes[0][1]
    pitch = (line_boxes[-1][1] - line_boxes[0][1]) / (n - 1) if n > 1 else pitch_default
    if pitch <= 0: pitch = pitch_default
    top = line_boxes[0][1] + (h1 - pitch) / 2
    return {"lines": lines, "x0": min(b[0] for b in line_boxes), "x1": max(b[2] for b in line_boxes),
            "y0": top, "y1": top + n * pitch, "pitch": pitch, "st": st, "role": None,
            "align": detect_align([(b[0], b[2]) for b in line_boxes], tol)}


# ---------- PDF pages with a text layer ----------
def parse_font(name, flags):
    n = re.sub(r"^[A-Z]{6}\+", "", name)
    fam = re.split(r"[-,]", n)[0]
    fam = re.sub(r"(PS)?(MT)?$", "", fam)
    fam = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", fam).strip()
    low = name.lower()
    gen = "monospace" if flags & 8 else "serif" if flags & 4 else "sans-serif"
    return fam, gen, bool(flags & 16) or "bold" in low, bool(flags & 2) or "italic" in low or "oblique" in low


def pdf_page(page):
    import fitz
    W, H = page.rect.width, page.rect.height
    if sum(len(s["text"].strip()) for b in page.get_text("dict")["blocks"] if b["type"] == 0
           for l in b["lines"] for s in l["spans"]) < 5:
        return None                                        # no text layer -> OCR path
    small = None
    if USE_CLAUDE:
        z = 1568 / W
        small = pix_to_pil(page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False))
    blocks = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0: continue
        lines, boxes, rects = [], [], []
        for l in b["lines"]:
            if abs(l["dir"][1]) > 0.1: continue             # rotated text stays in the background image
            sp = list(l["spans"])
            while sp and not sp[0]["text"].strip(): sp.pop(0)
            while sp and not sp[-1]["text"].strip(): sp.pop()
            if not sp: continue
            parts = []
            for s in sp:
                fam, gen, bold, ital = parse_font(s["font"], s["flags"])
                parts.append([s["text"], (fam, gen, s["size"], "#%06x" % s["color"], bold, ital)])
            parts[0][0] = parts[0][0].lstrip(); parts[-1][0] = parts[-1][0].rstrip()
            lines.append([tuple(p) for p in parts]); boxes.append(tuple(l["bbox"]))
            rects += [fitz.Rect(s["bbox"]) for s in sp]
        if not lines: continue
        cnt = Counter()
        for ln in lines:
            for t, st in ln: cnt[st] += len(t)
        st = cnt.most_common(1)[0][0]
        blk = make_block(lines, boxes, st, boxes[0][3] - boxes[0][1], W * 0.006)
        blk["erase"] = rects; blocks.append(blk)

    def bg_fn(kept):                                        # redact the kept text -> clean background, no inpainting
        for b in kept:
            for r in b["erase"]: page.add_redact_annot(r, fill=False)
        try:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
        except TypeError:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
        z = MAX_W / W
        return pix_to_pil(page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False))
    return blocks, bg_fn, W, H, small, True


def pix_to_pil(pix):
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


# ---------- images / scanned pages: OCR ----------
def analyse(arr, box, pad=4):
    x0, y0, x1, y1 = [int(v) for v in box]
    x0, y0, x1, y1 = max(x0 - pad, 0), max(y0 - pad, 0), x1 + pad, y1 + pad
    crop = arr[y0:y1, x0:x1].reshape(-1, 3).astype(int)
    q = (crop // 16) * 16
    vals, cnt = np.unique(q, axis=0, return_counts=True)
    bg = np.median(crop[(q == vals[cnt.argmax()]).all(1)], axis=0)
    dist = np.linalg.norm(crop - bg, axis=1)
    fg = np.median(crop[dist >= np.percentile(dist, 92)], axis=0)
    return (x0, y0, x1, y1), bg, fg


def _lines_from(d, f, ox=0, oy=0, minconf=60):
    groups = defaultdict(list)
    for i, t in enumerate(d["text"]):
        if t.strip() and float(d["conf"][i]) >= minconf:
            groups[(d["block_num"][i], d["par_num"][i], d["line_num"][i])].append(i)
    out = []
    for idx in groups.values():
        idx.sort(key=lambda i: d["left"][i])
        text = " ".join(d["text"][i] for i in idx)
        alnum = sum(c.isalnum() for c in text)
        if alnum < 2 or alnum / len(text.replace(" ", "")) < 0.6: continue          # icons / rules / noise
        if sum(c in "0Oo" for c in text) > 0.5 * len(text) and len(text) >= 4: continue
        out.append({"text": text, "box": [ox + min(d["left"][i] for i in idx) / f, oy + min(d["top"][i] for i in idx) / f,
                                          ox + max(d["left"][i] + d["width"][i] for i in idx) / f,
                                          oy + max(d["top"][i] + d["height"][i] for i in idx) / f]})
    return out


def _overlap(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0]); h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0: return 0
    return w * h / min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))


def region_ocr(arr, found):
    """Second pass: find coloured shapes (buttons, bars, banners) and OCR inside each with text/background polarity normalised."""
    H, W = arr.shape[:2]
    q = arr // 32
    key = q[:, :, 0].astype(int) * 64 + q[:, :, 1] * 8 + q[:, :, 2]
    cands = []
    for k in np.unique(key):
        m = (key == k).astype(np.uint8)
        if m.sum() < W * H * 0.002: continue
        n, _, st, _ = cv2.connectedComponentsWithStats(m, connectivity=4)
        for i in range(1, n):
            x, y, w, h, a = st[i]
            if a >= W * H * 0.002 and w < W * 0.95 and h < H * 0.6 and h > 14: cands.append((a, x, y, w, h))
    out = []
    for a, x, y, w, h in sorted(cands, reverse=True)[:30]:
        crop = cv2.cvtColor(arr[y + 3:y + h - 3, x + 3:x + w - 3], cv2.COLOR_RGB2GRAY)
        if crop.size == 0: continue
        _, bw = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if (bw == 0).mean() > 0.5: bw = 255 - bw                              # text = minority = black
        f = 3 if crop.shape[0] < 120 else 1
        bw = cv2.copyMakeBorder(cv2.resize(bw, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC), 12, 12, 12, 12,
                                cv2.BORDER_CONSTANT, value=255)
        d = pytesseract.image_to_data(bw, output_type=pytesseract.Output.DICT, config="--oem 1 --psm 6")
        for l in _lines_from(d, f, 0, 0, 65):
            b = l["box"]; l["box"] = [x + 3 + b[0] - 12 / f, y + 3 + b[1] - 12 / f, x + 3 + b[2] - 12 / f, y + 3 + b[3] - 12 / f]
            if not any(_overlap(l["box"], o["box"]) > 0.3 for o in found + out): out.append(l)
    return out


def ocr_lines(arr, f):
    found = []
    variants = [arr] + [arr[:, :, c] for c in (0, 1, 2)]                       # RGB + each channel (white-on-colour text)
    for v in variants:
        v = cv2.resize(v, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC) if f != 1 else v
        d = pytesseract.image_to_data(v, output_type=pytesseract.Output.DICT, config="--oem 1 --psm 11")
        for l in _lines_from(d, f):
            dup = [o for o in found if _overlap(l["box"], o["box"]) > 0.5]
            if not dup: found.append(l)
    found += region_ocr(arr, found)
    return sorted(found, key=lambda l: (l["box"][1], l["box"][0]))


def tighten(arr, box, pad=3):
    """Shrink an OCR box to the real ink (ignores long thin rules that touch the box)."""
    (x0, y0, x1, y1), bg, _ = analyse(arr, box, pad)
    reg = arr[y0:y1, x0:x1].astype(int)
    ink = np.linalg.norm(reg - bg, axis=2) > 60
    ink[ink.mean(axis=1) > 0.9, :] = False
    ink[:, ink.mean(axis=0) > 0.9] = False
    ys, xs = np.where(ink)
    if len(xs) < 5: return box
    return [x0 + xs.min(), y0 + ys.min(), x0 + xs.max() + 1, y0 + ys.max() + 1]


def group_paragraphs(lines):
    paras = []
    for l in lines:
        x0, y0, x1, y1 = l["box"]; h = y1 - y0
        for p in paras:
            lx0, ly0, lx1, ly1 = p[-1]["box"]; lh = ly1 - ly0
            gap = y0 - ly1
            if (-0.3 * lh <= gap <= 0.9 * lh and 0.8 <= h / lh <= 1.25
                    and (abs(x0 - lx0) < 0.8 * h or abs((x0 + x1) - (lx0 + lx1)) / 2 < 0.8 * h)):
                p.append(l); break
        else:
            paras.append([l])
    return paras


def ocr_page(img):
    w, h = img.size
    if w > MAX_W:
        img = img.resize((MAX_W, round(h * MAX_W / w)), Image.LANCZOS); w, h = img.size
    arr = np.array(img)
    lines = ocr_lines(arr, 2 if w < 1400 else 1)
    for l in lines: l["box"] = tighten(arr, l["box"])
    blocks = []
    for para in group_paragraphs(lines):
        ub = (min(l["box"][0] for l in para), min(l["box"][1] for l in para),
              max(l["box"][2] for l in para), max(l["box"][3] for l in para))
        _, _, fg = analyse(arr, ub)
        colour = "#%02x%02x%02x" % tuple(int(v) for v in fg)
        size = float(np.median([l["box"][3] - l["box"][1] for l in para]))
        st = (DEFAULT_FONT, "sans-serif", size, colour, False, False)
        boxes = [tuple(l["box"]) for l in para]
        blocks.append(make_block([[(l["text"], st)] for l in para], boxes, st, size * 1.4, w * 0.006))
        blocks[-1]["erase"] = [l["box"] for l in para]

    def bg_fn(kept):                                        # inpaint only the text we turned into HTML
        mask = np.zeros(arr.shape[:2], np.uint8)
        for b in kept:
            for box in b["erase"]:
                (x0, y0, x1, y1), bg, _ = analyse(arr, box)
                reg = arr[y0:y1, x0:x1].astype(int)
                mask[y0:y1, x0:x1] |= (np.linalg.norm(reg - bg, axis=2) > 40).astype(np.uint8) * 255
        return Image.fromarray(cv2.inpaint(arr, cv2.dilate(mask, np.ones((5, 5), np.uint8)), 3, cv2.INPAINT_TELEA))
    small = img.copy(); small.thumbnail((1568, 1568))
    return blocks, bg_fn, w, h, small, False


# =====================================================================================
# 2. SEMANTICS  (Claude vision if available, heuristics otherwise)
# =====================================================================================
def claude_enrich(small, blocks, Hc, is_pdf):
    import anthropic
    items = [{"id": i, "text": "\n".join("".join(t for t, _ in ln) for ln in b["lines"]),
              "box_pct": [round(b["x0"], 1), round(b["y0"] / Hc * 100, 1), round(b["x1"], 1), round(b["y1"] / Hc * 100, 1)]}
             for i, b in enumerate(blocks)]
    prompt = (
        "This is one slide/page. Below are the text blocks detected on it (ids, text, bounding boxes in % of the page).\n"
        "Return ONLY JSON: {\"font\": \"<closest Google Fonts family for the body text>\", \"blocks\": "
        "[{\"id\": 0, \"role\": \"h1|h2|h3|p|span|button|label|graphic\", \"bold\": false, \"text\": \"...\"}]}\n"
        "Rules: choose roles by visual hierarchy and function (headline=h1, sub-headings=h2/h3, paragraphs=p, "
        "short labels or numbers=span, text sitting inside a button-like shape=button, text that is part of a logo, icon or illustration=graphic). "
        + ("Keep text exactly as given. " if is_pdf else "Fix obvious OCR mistakes only, keep line breaks, and keep placeholders like {{name}} verbatim. ")
        + "Only one h1.\n\nBLOCKS:\n" + json.dumps(items, ensure_ascii=False))
    buf = io.BytesIO(); small.save(buf, "PNG")
    msg = anthropic.Anthropic().messages.create(
        model=CLAUDE_MODEL, max_tokens=3000,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(buf.getvalue()).decode()}},
            {"type": "text", "text": prompt}]}])
    data = json.loads(re.search(r"\{.*\}", msg.content[0].text, re.S).group(0))
    for r in data.get("blocks", []):
        b = blocks[r["id"]]
        b["role"] = r.get("role")
        if r.get("bold") and not is_pdf:
            b["st"] = b["st"][:4] + (True,) + b["st"][5:]
        txt = r.get("text", "")
        if not is_pdf and len(txt.split("\n")) == len(b["lines"]):
            for ln, t in zip(b["lines"], txt.split("\n")):
                ln[:] = [(t, ln[0][1])]
    return data.get("font")


def heuristic_roles(blocks, Hc):
    if not blocks: return
    med = sorted(b["st"][2] for b in blocks)[len(blocks) // 2]
    h1_done = False
    for b in sorted(blocks, key=lambda b: -b["st"][2]):
        if b["role"]: continue
        s, words = b["st"][2], sum(len(t.split()) for ln in b["lines"] for t, _ in ln)
        if s >= 1.4 * med and not h1_done and b["y0"] < Hc * .55: b["role"], h1_done = "h1", True
        elif s >= 1.2 * med: b["role"] = "h2"
        elif len(b["lines"]) == 1 and words <= 3: b["role"] = "span"
        else: b["role"] = "p"


# =====================================================================================
# 3. LAYOUT TREE (recursive XY-cut) and HTML/CSS emission
# =====================================================================================
class Node:
    def __init__(self, kind, kids=None, block=None):
        self.kind, self.kids, self.block = kind, kids or [], block
        src = [block] if block else kids
        self.x0 = min(s["x0"] if block else s.x0 for s in src); self.y0 = min(s["y0"] if block else s.y0 for s in src)
        self.x1 = max(s["x1"] if block else s.x1 for s in src); self.y1 = max(s["y1"] if block else s.y1 for s in src)


def cut(items, ax):
    k = (lambda n: (n.x0, n.x1)) if ax == 0 else (lambda n: (n.y0, n.y1))
    items = sorted(items, key=lambda n: k(n)[0])
    groups, end = [[items[0]]], k(items[0])[1]
    for n in items[1:]:
        s, e = k(n)
        if s >= end + EPS: groups.append([n]); end = e
        else: groups[-1].append(n); end = max(end, e)
    return groups


def build(items):
    if len(items) == 1: return items[0]
    for ax, kind in ((1, "col"), (0, "row")):
        g = cut(items, ax)
        if len(g) > 1: return Node(kind, [build(x) for x in g])
    return Node("row", sorted(items, key=lambda n: (n.x0, n.y0)))      # overlapping: negative margins


def u(v):
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "0", "-0") else s + "cqw"


def lead(a, b):
    """-> (align-self, leading margin) for one axis given gap before (a) and after (b)."""
    if a > CENTER_TOL and abs(a - b) <= CENTER_TOL: return "center", 0
    if a > CENTER_TOL and b <= CENTER_TOL: return "flex-end", 0
    return None, a


class Ctx:
    def __init__(self): self.rules, self.typo, self.inline, self.n = [], {}, {}, 0

    def nid(self): self.n += 1; return f"n{self.n}"

    def typo_class(self, st):
        if st not in self.typo:
            fam, gen, size, colour, bold, ital = st
            stack = f'"{fam}",{gen}' if fam else gen
            name = f"t{len(self.typo) + 1}"
            self.typo[st] = name
            self.rules.append(f".{name}{{font-family:{stack};font-size:{u(size)};color:{colour};"
                              f"font-weight:{700 if bold else 400};font-style:{'italic' if ital else 'normal'}}}")
        return self.typo[st]

    def inline_class(self, st):
        key = st[3:]
        if key not in self.inline:
            name = f"s{len(self.inline) + 1}"; self.inline[key] = name
            self.rules.append(f".{name}{{color:{key[0]};font-weight:{700 if key[1] else 400};"
                              f"font-style:{'italic' if key[2] else 'normal'}}}")
        return self.inline[key]


def render(node, pb, prev, direction, ctx, depth, Hc, idx=0, total=1):
    decl = []
    if direction == "col":
        mt, lg, rg = node.y0 - (prev.y1 if prev else pb[1]), node.x0 - pb[0], pb[2] - node.x1
        sa, ml = lead(lg, rg)
        if abs(mt) > .005: decl.append("margin-top:" + u(mt))
        if abs(ml) > .005: decl.append("margin-left:" + u(ml))
    else:
        ml, tg, bg = node.x0 - (prev.x1 if prev else pb[0]), node.y0 - pb[1], pb[3] - node.y1
        sa, mt = lead(tg, bg)
        if abs(ml) > .005: decl.append("margin-left:" + u(ml))
        if abs(mt) > .005: decl.append("margin-top:" + u(mt))
    if sa: decl.append("align-self:" + sa)
    decl += ["width:" + u(node.x1 - node.x0), "height:" + u(node.y1 - node.y0)]
    cid, ind = ctx.nid(), "  " * (depth + 1)

    if node.kind == "leaf":
        b = node.block
        decl.append("line-height:" + u(b["pitch"]))
        if b["align"] != "left": decl.append("text-align:" + b["align"])
        ctx.rules.append(f".{cid}{{{';'.join(decl)}}}")
        tag = TAGS.get(b["role"], "p")
        lines = []
        for ln in b["lines"]:
            parts = [f'<span class="{ctx.inline_class(st)}">{html.escape(t)}</span>' if st[3:] != b["st"][3:]
                     else html.escape(t) for t, st in ln]
            lines.append("".join(parts))
        extra = ' type="button"' if tag == "button" else ""
        return (f'{ind}<{tag} class="t {ctx.typo_class(b["st"])} {cid}"{extra}'
                f'{" data-fit" if FIT_TEXT else ""}>{"<br>".join(lines)}</{tag}>')

    ctx.rules.append(f".{cid}{{{';'.join(decl)}}}")
    tag = "div"
    if depth == 1:
        tag = "header" if idx == 0 and node.y1 <= Hc * .25 else "footer" if idx == total - 1 and node.y0 >= Hc * .8 else "section"
    box, prevk, out = (node.x0, node.y0, node.x1, node.y1), None, []
    for k in node.kids:
        out.append(render(k, box, prevk, node.kind, ctx, depth + 1, Hc)); prevk = k
    return f'{ind}<{tag} class="{node.kind} {cid}">\n' + "\n".join(out) + f"\n{ind}</{tag}>"


FIT_JS = """<script>
/* match each text's font-size to its measured width (fonts on the web differ from the source) */
(function(){var s=document.querySelector('.slide');
function fit(){var cw=s.clientWidth/100;document.querySelectorAll('[data-fit]').forEach(function(e){
 var r=document.createRange();r.selectNodeContents(e);var w=r.getBoundingClientRect().width,t=e.clientWidth;
 if(w>0&&t>0){var k=Math.min(1.6,Math.max(.6,t/w));e.style.fontSize=(parseFloat(getComputedStyle(e).fontSize)*k/cw)+'cqw';}});}
(document.fonts&&document.fonts.ready?document.fonts.ready:Promise.resolve()).then(fit);})();
</script>"""


def build_package(blocks, bg_fn, W, H, small, is_pdf) -> bytes:
    sw, sh = snap(W, H)
    kx, Hc = 100 / W, 100 * sh / sw
    ky = Hc / H
    for b in blocks:                                          # normalise: x -> cqw, y -> cqw (after ratio snap)
        b["x0"] *= kx; b["x1"] *= kx; b["y0"] *= ky; b["y1"] *= ky; b["pitch"] *= ky
        sc = lambda st: (st[0], st[1], st[2] * kx) + st[3:]
        b["st"] = sc(b["st"]); b["lines"] = [[(t, sc(st)) for t, st in ln] for ln in b["lines"]]

    font = None
    if USE_CLAUDE and blocks:
        try: font = claude_enrich(small, blocks, Hc, is_pdf)
        except Exception as e: print("Claude step skipped:", e)
    if not is_pdf and font:
        for b in blocks:
            b["st"] = (font,) + b["st"][1:]; b["lines"] = [[(t, (font,) + st[1:]) for t, st in ln] for ln in b["lines"]]
    blocks = [b for b in blocks if b["role"] != "graphic"]      # logo / icon text stays inside the background image
    heuristic_roles(blocks, Hc)
    bg = bg_fn(blocks)

    ctx, body = Ctx(), ""
    if blocks:
        root = build([Node("leaf", block=b) for b in blocks])
        top = root.kids if root.kind == "col" else [root]
        pb, prev, out = (0, 0, 100, Hc), None, []
        for i, n in enumerate(top):
            out.append(render(n, pb, prev, "col", ctx, 1, Hc, i, len(top))); prev = n
        body = "\n".join(out)

    fams = sorted({st[0] for st in ctx.typo if st[0] not in SYSTEM_FONTS})
    imp = ("@import url('https://fonts.googleapis.com/css2?" + "&".join(
        f"family={f.replace(' ', '+')}:wght@400;700" for f in fams) + "&display=swap');\n") if fams else ""
    index = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Slide</title>
  <link rel="stylesheet" href="css/style.css">
</head>
<body>
<main class="slide">
{body}
</main>
{FIT_JS if FIT_TEXT else ""}
</body>
</html>"""
    css = f"""{imp}*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{height:100%;background:#111}}
body{{display:grid;place-items:center}}
.slide{{container-type:inline-size;display:flex;flex-direction:column;overflow:hidden;
  width:min(100vw,calc(100vh*{sw}/{sh}));aspect-ratio:{sw} / {sh};
  background:url('../images/background.{IMG_EXT}') center/100% 100% no-repeat}}
.slide>*,.row>*,.col>*{{flex:none}}
.row{{display:flex;flex-direction:row}}
.col{{display:flex;flex-direction:column}}
.t{{white-space:nowrap}}
button.t{{background:none;border:0;cursor:pointer}}
{chr(10).join(ctx.rules)}
"""
    buf = io.BytesIO(); bg.convert("RGB").save(buf, IMG_FMT, quality=90)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("index.html", index); z.writestr("css/style.css", css)
        z.writestr(f"images/background.{IMG_EXT}", buf.getvalue())
    return out.getvalue()


# =====================================================================================
# 4. API
# =====================================================================================
def pages(data, name):
    if name.lower().endswith(".pdf"):
        import fitz
        for page in fitz.open(stream=data, filetype="pdf"):
            r = pdf_page(page)
            if r is None:                                    # scanned page -> OCR path
                z = 2000 / page.rect.width
                r = ocr_page(pix_to_pil(page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)))
            yield r
    else:
        yield ocr_page(Image.open(io.BytesIO(data)).convert("RGB"))


def make_app():
    from fastapi import FastAPI, File, UploadFile
    from fastapi.responses import HTMLResponse, Response
    app = FastAPI()

    @app.post("/convert")
    async def convert(file: UploadFile = File(...)):
        data, out = await file.read(), io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for n, r in enumerate(pages(data, file.filename), 1):
                z.writestr(f"page_{n:02d}.zip", build_package(*r))
        return Response(out.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="iva_packages.zip"'})

    @app.get("/", response_class=HTMLResponse)
    def form():
        return """<h2>PDF / screenshot to semantic HTML packages</h2>
<form action="/convert" method="post" enctype="multipart/form-data">
<input type="file" name="file" accept=".pdf,image/*" required> <button>Convert</button></form>"""
    return app


try:
    app = make_app()
except ImportError:      # lets you import the converter functions without FastAPI installed
    app = None
