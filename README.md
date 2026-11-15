# pdf-image-converter
Converts the image of PDF intp HTML


ou need Python packages plus one system program, Tesseract OCR.

1. Python packages

Python 3.9 or newer works. Save this as requirements.txt:

fastapi
uvicorn
python-multipart
pymupdf
opencv-python-headless
pytesseract
pillow
numpy

Install them in a virtual environment:

bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
Package	Used for
fastapi, uvicorn	The web server and upload endpoint
python-multipart	Required by FastAPI to receive file uploads
pymupdf	Rendering PDF pages to images. It needs no Poppler or other extra install.
opencv-python-headless	Painting out the text from the background image
pytesseract	Python wrapper that calls Tesseract
pillow, numpy	Image handling and colour sampling

Use opencv-python-headless rather than opencv-python on a server, since it has no GUI dependencies.

2. Tesseract OCR (system program)

pytesseract is only a wrapper, so Tesseract itself must be installed separately:

Ubuntu/Debian: sudo apt install tesseract-ocr
macOS: brew install tesseract
Windows: download the installer from the UB Mannheim Tesseract build, then add its install folder to your PATH. Alternatively, add this line near the top of app.py:
python
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
Other languages: install the matching language data (e.g. tesseract-ocr-ara on Ubuntu), then pass lang="eng+ara" in the image_to_data call.

Check it works with tesseract --version.

3. Run it
bash
uvicorn app:app --reload

Then open http://127.0.0.1:8000.
