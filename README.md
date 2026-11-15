# pdf-image-converter
Converts the image of PDF intp HTML

## Installation & Setup

This application uses Python, FastAPI, Tesseract OCR, and the Anthropic Claude API to process PDF documents and generate enhanced HTML output.

### 1. Requirements

- Python 3.9 or newer
- Tesseract OCR
- Anthropic API key

### 2. Install Python Dependencies

Create a `requirements.txt` file with the following packages:

```txt
fastapi
uvicorn
python-multipart
pymupdf
opencv-python-headless
pytesseract
pillow
numpy
anthropic
```

Create and activate a virtual environment, then install the dependencies:

```bash
# Create virtual environment
python -m venv venv

# Activate (Linux / macOS)
source venv/bin/activate

# Windows
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

**Package overview:**

| Package | Purpose |
|---|---|
| `fastapi`, `uvicorn` | Web server and API |
| `python-multipart` | File uploads |
| `pymupdf` | PDF-to-image conversion |
| `opencv-python-headless` | Image processing and text removal |
| `pytesseract` | OCR integration |
| `pillow`, `numpy` | Image handling and colour processing |
| `anthropic` | Claude API integration for improved HTML structure |

### 3. Install Tesseract OCR

Tesseract must be installed separately from the Python packages.

**Ubuntu / Debian**
```bash
sudo apt install tesseract-ocr
```

**macOS**
```bash
brew install tesseract
```

**Windows**

Download and install Tesseract from the [UB Mannheim Tesseract builds](https://github.com/UB-Mannheim/tesseract/wiki), then add its installation directory to your system PATH.

Verify the installation:

```bash
tesseract --version
```

For additional language support, install the corresponding Tesseract language data.

### 4. Configure Anthropic Claude API

The application uses the Anthropic Claude API to enhance the generated HTML, including better content structuring and semantic heading identification.

**Step 1: Get an API key**

1. Create an account at the [Anthropic Console](https://console.anthropic.com/).
2. Generate an API key from the API keys section.
3. Ensure API billing or credits are configured.

**Step 2: Configure the API key**

The application reads the key from the `ANTHROPIC_API_KEY` environment variable.

On macOS / Linux:

```bash
export ANTHROPIC_API_KEY="sk-ant-your-key-here"
```

On Windows PowerShell:

```powershell
$env:ANTHROPIC_API_KEY="sk-ant-your-key-here"
```

Alternatively, you can use a `.env` file by installing `python-dotenv`:

```bash
pip install python-dotenv
```

Create `.env` in the project directory:

```env
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

Add the following to the beginning of `app.py`:

```python
from dotenv import load_dotenv
load_dotenv()
```

Make sure `.env` is included in `.gitignore` to prevent accidental exposure of your API key.

**Note:** The Claude enhancement step is optional at runtime. If the API call fails, the application skips that step and continues the standard conversion process. Check the terminal for messages beginning with `Claude step skipped:` if troubleshooting is needed.

### 5. Run the Application

Start the FastAPI server:

```bash
uvicorn app:app --reload
```

Access the application at:

**http://127.0.0.1:8000**

API documentation:

**http://127.0.0.1:8000/docs**

### 6. Docker Deployment (Optional)

A basic Dockerfile:

```dockerfile
FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y tesseract-ocr && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and run:

```bash
docker build -t pdf-ocr-app .

docker run -p 8000:8000 \
  -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
  pdf-ocr-app
```

For production deployments, configure the API key through your hosting platform's environment variables or secrets manager rather than hardcoding it.

### 7. Additional Notes

- **API Security:** Never commit API keys or `.env` files to GitHub.
- **Language Support:** Install the appropriate Tesseract language data for multilingual OCR.
- **Offline HTML:** Exported HTML packages require no Python dependencies. They currently load the Oswald font from Google Fonts. For offline use, download the font and include it locally using CSS `@font-face`.
- **Future Enhancements:** Background processing for large PDFs can be implemented using Celery with Redis or ARQ.
