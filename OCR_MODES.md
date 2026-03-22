# OCR Modes - Local vs Production

## Overview

FamPilot uses **different OCR methods** depending on the environment:

- **Local Development**: Tesseract OCR → Claude text extraction
- **Production (Render/Cloud)**: Claude Vision API (direct image analysis)

This allows deployment without system package installation.

---

## How It Works

### Automatic Detection

```python
from main import TESSERACT_AVAILABLE

if TESSERACT_AVAILABLE:
    # Local: Use Tesseract OCR
    text = extract_text_from_image(image_path)
    event = extract_event_details(text, api_key)
else:
    # Production: Use Vision API
    event = extract_event_from_image_vision(image_path, api_key)
```

### Local Development (Tesseract Available)

```
Image → Tesseract OCR → Extract Text → Claude API → Event Details
```

**Process:**
1. Upload image
2. Tesseract extracts text from image
3. Text sent to Claude for parsing
4. Returns structured event data

**Pros:**
- Faster (2 steps instead of 1)
- Lower API costs (text-only vs vision)
- More accurate text extraction

**Cons:**
- Requires Tesseract installation
- System dependency

### Production (Tesseract Not Available)

```
Image → Claude Vision API → Event Details
```

**Process:**
1. Upload image
2. Image sent directly to Claude Vision
3. Claude analyzes image and extracts event details
4. Returns structured event data

**Pros:**
- No system dependencies
- Works on any platform
- Easier deployment
- Single API call

**Cons:**
- Higher API costs (vision vs text)
- Slightly slower

---

## Environment Detection

### Check Tesseract Availability

```python
from main import TESSERACT_AVAILABLE

print(f"Tesseract available: {TESSERACT_AVAILABLE}")
```

**Returns:**
- `True` - Tesseract installed and working
- `False` - Tesseract not available, will use vision

### How Detection Works

```python
# In main.py
TESSERACT_AVAILABLE = False
try:
    pytesseract.get_tesseract_version()
    TESSERACT_AVAILABLE = True
except:
    TESSERACT_AVAILABLE = False
```

---

## Local Setup (Optional Tesseract)

### With Tesseract (Recommended for Development)

```bash
# Install Tesseract
brew install tesseract  # macOS
# sudo apt-get install tesseract-ocr  # Linux

# Install Python packages
pip install -r requirements.txt

# Run
python3 app.py
```

**Result:** Uses fast OCR path

### Without Tesseract (Production-like)

```bash
# Just install Python packages
pip install -r requirements.txt

# Run
python3 app.py
```

**Result:** Uses Vision API (same as production)

---

## Production Deployment (Render)

### No System Packages Needed

```yaml
# render.yaml
buildCommand: "pip install -r requirements.txt"  # No apt-get!
startCommand: "uvicorn app:app --host 0.0.0.0 --port $PORT"
```

### Automatic Fallback

1. Render builds without Tesseract
2. `TESSERACT_AVAILABLE = False`
3. App uses Vision API automatically
4. Works perfectly!

**No build.sh needed** ✅

---

## API Cost Comparison

### Tesseract Path (Local)

```
Request 1: Image → Tesseract → Text (free)
Request 2: Text → Claude API → Event ($0.003)

Total: ~$0.003 per image
```

### Vision Path (Production)

```
Request 1: Image → Claude Vision → Event ($0.048)

Total: ~$0.048 per image
```

**Cost difference:** ~16x more for vision
**Tradeoff:** Worth it for easy deployment

---

## Function Reference

### extract_text_from_image(image_path)

**Local only** - Requires Tesseract

```python
from main import extract_text_from_image

text = extract_text_from_image("event.png")
# Returns: "Summer Festival\nJuly 20, 2024\n..."
```

**Raises:**
- `Exception` if Tesseract not available
- `FileNotFoundError` if image not found

### extract_event_from_image_vision(image_path, api_key)

**Production** - No Tesseract needed

```python
from main import extract_event_from_image_vision

event = extract_event_from_image_vision("event.png", api_key)
# Returns: {"title": "Summer Festival", "start_date": "2024-07-20", ...}
```

**Works:**
- Anywhere (local or production)
- No system dependencies
- Direct image analysis

### extract_event_details(text, api_key)

**Text extraction** - Used after OCR

```python
from main import extract_event_details

text = "Summer Festival\nJuly 20, 2024\n10AM-4PM"
event = extract_event_details(text, api_key)
# Returns: {"title": "Summer Festival", ...}
```

---

## Supported Image Formats

Both methods support:
- PNG (.png)
- JPEG (.jpg, .jpeg)
- GIF (.gif)
- WebP (.webp)

---

## Testing Both Paths

### Test Tesseract Path

```bash
# Ensure Tesseract is installed
tesseract --version

# Run app
python3 app.py

# Upload image
# Check logs for: "Using Tesseract OCR"
```

### Test Vision Path

```bash
# Simulate production (disable Tesseract)
# Rename tesseract binary temporarily
sudo mv /opt/homebrew/bin/tesseract /opt/homebrew/bin/tesseract.bak

# Run app
python3 app.py

# Upload image
# Check logs for: "Using Vision API"

# Restore
sudo mv /opt/homebrew/bin/tesseract.bak /opt/homebrew/bin/tesseract
```

---

## Error Handling

### Tesseract Not Found (Expected in Production)

```
TESSERACT_AVAILABLE = False
→ Automatically uses Vision API
→ No error, seamless fallback
```

### Vision API Error

```
Error calling Claude Vision API: ...
→ Shows error to user
→ Suggests retrying with different image
```

### Image Format Error

```
Error: Unsupported image format
→ Verify image is PNG/JPEG/GIF/WebP
```

---

## Migration Path

If you previously used build.sh:

### Old Deployment (build.sh)

```yaml
buildCommand: "./build.sh"  # Installs Tesseract
```

### New Deployment (No build.sh)

```yaml
buildCommand: "pip install -r requirements.txt"  # Just Python
```

**Changes:**
- ❌ Remove build.sh
- ✅ Use Vision API automatically
- ✅ Faster builds
- ✅ More reliable

---

## Best Practices

### Development

✅ **Install Tesseract locally**
- Faster iteration
- Lower API costs
- Better for testing

### Production

✅ **Use Vision API**
- No system dependencies
- Simpler deployment
- More reliable

### Hybrid

✅ **Support both**
- Code automatically detects
- Best of both worlds
- Seamless experience

---

## Troubleshooting

### "Tesseract not installed" on Local

```bash
brew install tesseract  # macOS
sudo apt-get install tesseract-ocr  # Linux
```

### Vision API Slow

Normal - vision processing takes 3-5 seconds vs 1-2 for OCR

### High API Costs

Consider installing Tesseract locally for development:
```bash
brew install tesseract
```

Reduces costs by ~16x for testing.

---

## Performance Comparison

| Metric | Tesseract Path | Vision Path |
|--------|----------------|-------------|
| **Speed** | ~2 seconds | ~4 seconds |
| **API Cost** | $0.003/image | $0.048/image |
| **Setup** | Install Tesseract | None |
| **Accuracy** | Very High | Very High |
| **Deployment** | Complex | Simple |

---

## Summary

**Local Development:**
```
Install Tesseract → Fast & Cheap OCR
```

**Production:**
```
No Installation → Vision API Works Automatically
```

**Code:**
```python
# Automatically handles both!
if TESSERACT_AVAILABLE:
    use_tesseract_path()
else:
    use_vision_path()
```

---

Built for flexibility and easy deployment ✨
