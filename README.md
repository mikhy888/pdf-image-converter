# pdf-image-converter
Converts the image of PDF intp HTML

## Software Requirements & Setup

This project requires Python packages and one additional system-level program: **Tesseract OCR**.

### 1. Python Requirements

Python **3.9 or newer** is required.

Create a `requirements.txt` file in your project root directory with the following dependencies:

```txt
fastapi
uvicorn
python-multipart
pymupdf
opencv-python-headless
pytesseract
pillow
numpy
```

#### Install Dependencies

It is recommended to use a Python virtual environment.

```bash
# Create a virtual environment
python -m venv venv

# Activate the virtual environment

# Linux / macOS
source venv/bin/activate

# Windows
venv\Scripts\activate

# Install Python dependencies
pip install -r requirements.txt
```

#### Package Overview

| Package | Purpose |
|---|---|
| `fastapi` | Provides the web framework and API endpoints. |
| `uvicorn` | Runs the FastAPI application. |
| `python-multipart` | Enables file uploads through FastAPI. |
| `pymupdf` | Converts PDF pages into images without requiring Poppler or additional rendering tools. |
| `opencv-python-headless` | Processes images and removes text from background images. |
| `pytesseract` | Python wrapper for the Tesseract OCR engine. |
| `pillow` | Provides image processing and manipulation functionality. |
| `numpy` | Supports image processing and colour sampling operations. |

> **Note:** Use `opencv-python-headless` instead of `opencv-python` when deploying on a server. It does not include unnecessary GUI dependencies.

### 2. Install Tesseract OCR

`pytesseract` is only a Python wrapper. The actual **Tesseract OCR engine** must be installed separately on your system.

Install Tesseract according to your operating system:

#### Ubuntu / Debian

```bash
sudo apt update
sudo apt install tesseract-ocr
```

#### macOS

```bash
brew install tesseract
```

#### Windows

1. Download the Tesseract installer from the [UB Mannheim Tesseract OCR builds](https://github.com/UB-Mannheim/tesseract/wiki).
2. Install Tesseract OCR.
3. Add the Tesseract installation directory to your system's `PATH` environment variable.

Alternatively, if Tesseract is not available in your system's `PATH`, specify its executable location near the top of `app.py`:

```python
import pytesseract

pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)
```

#### Additional Language Support

For OCR processing in languages other than English, install the corresponding Tesseract language data.

For example, on Ubuntu:

```bash
sudo apt install tesseract-ocr-ara
```

You can specify multiple languages in your OCR processing code:

```python
pytesseract.image_to_data(image, lang="eng+ara")
```

Replace `ara` with the appropriate language code when needed.

#### Verify Installation

Run the following command to verify that Tesseract is installed correctly:

```bash
tesseract --version
```

### 3. Run the Application

Once all dependencies and Tesseract OCR are installed, start the FastAPI application using:

```bash
uvicorn app:app --reload
```

The application will be available at:

**http://127.0.0.1:8000**

You can also access the automatically generated API documentation at:

**http://127.0.0.1:8000/docs**

### 4. Docker Deployment (Optional)

If you prefer to deploy the application using Docker, the following Dockerfile provides a basic setup.

Create a file named `Dockerfile` in your project root directory:

```dockerfile
FROM python:3.12-slim

# Install Tesseract OCR
RUN apt-get update && \
    apt-get install -y tesseract-ocr && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY app.py .

# Start FastAPI application
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build the Docker image:

```bash
docker build -t pdf-ocr-app .
```

Run the container:

```bash
docker run -p 8000:8000 pdf-ocr-app
```

The application will then be accessible at:

**http://localhost:8000**

> **Note:** If your application contains additional source files or directories, make sure to include them in the Docker image.

### 5. Optional Dependencies & Future Enhancements

The following dependencies can be added when extending the application's functionality.

| Dependency | Use Case | Installation |
|---|---|---|
| `anthropic` | Integrating the Claude API for AI-powered processing. | `pip install anthropic` |
| `celery` | Managing background tasks for large PDF processing jobs. | `pip install celery` |
| `redis` | Providing a message broker and backend for Celery tasks. | Install Redis separately. |
| `arq` | Managing asynchronous background jobs using Redis. | `pip install arq` |

### 6. Exported HTML Packages

The exported HTML packages do not require any additional Python dependencies or installation.

They currently load the **Oswald font** from Google Fonts.

For offline use (such as offline IVAs), download the Oswald font files and include them in each exported HTML package. Then configure the font using CSS `@font-face`.

Example:

```css
@font-face {
    font-family: 'Oswald';
    src: url('./fonts/Oswald-Regular.woff2') format('woff2');
    font-weight: 400;
    font-style: normal;
}

body {
    font-family: 'Oswald', sans-serif;
}
```

This ensures that exported HTML packages can display the intended typography without requiring an internet connection.
