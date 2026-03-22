# Troubleshooting Guide

## Common Issues and Solutions

---

## Issue: Jinja2Templates TypeError

### Error Message
```
TypeError: cannot use 'tuple' as a dict key
```

### Cause
This error occurs when using Python 3.9+ type hint syntax (`tuple[...]`) instead of the typing module (`Tuple[...]`).

### Solution
✅ **Fixed in current version**

Changed:
```python
# OLD (causes error in some Python versions)
def parse_time_simple(time_str: str, date_str: str) -> tuple[Optional[datetime], Optional[datetime]]:
```

To:
```python
# NEW (compatible)
from typing import Tuple

def parse_time_simple(time_str: str, date_str: str) -> Tuple[Optional[datetime], Optional[datetime]]:
```

### Verification
```bash
python3 test_app.py
```

Should show:
```
✓ PASS: Templates
  ✓ index.html exists
  ✓ result.html exists
  ✓ confirmed.html exists
```

---

## Issue: Templates Directory Not Found

### Error Message
```
jinja2.exceptions.TemplateNotFound: index.html
```

### Cause
- Templates directory doesn't exist
- Running from wrong directory
- Templates files missing

### Solution

1. **Verify templates directory exists:**
```bash
ls -la templates/
```

Should show:
```
index.html
result.html
confirmed.html
```

2. **Run from project root:**
```bash
cd /path/to/FamPilot
python3 app.py
```

3. **Recreate if missing:**
```bash
mkdir -p templates
# Re-download templates or restore from backup
```

---

## Issue: Module Import Errors

### Error Message
```
ModuleNotFoundError: No module named 'fastapi'
```

### Cause
Missing dependencies

### Solution
```bash
pip install -r requirements.txt
```

Or install individually:
```bash
pip install fastapi uvicorn python-multipart jinja2
pip install anthropic pytesseract Pillow
pip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client
```

---

## Issue: Tesseract Not Found

### Error Message
```
pytesseract.pytesseract.TesseractNotFoundError
```

### Cause
Tesseract OCR not installed

### Solution

**macOS:**
```bash
brew install tesseract
```

**Ubuntu/Debian:**
```bash
sudo apt-get install tesseract-ocr
```

**Windows:**
Download from [GitHub](https://github.com/UB-Mannheim/tesseract/wiki)

**Verify:**
```bash
tesseract --version
```

---

## Issue: API Key Not Set

### Error Message
```
✗ ANTHROPIC_API_KEY environment variable not set
```

### Cause
Environment variable not configured

### Solution
```bash
export ANTHROPIC_API_KEY='your-api-key-here'
```

**Persistent (add to ~/.zshrc or ~/.bashrc):**
```bash
echo 'export ANTHROPIC_API_KEY="your-key"' >> ~/.zshrc
source ~/.zshrc
```

**Verify:**
```bash
echo $ANTHROPIC_API_KEY
```

---

## Issue: Port Already in Use

### Error Message
```
OSError: [Errno 48] Address already in use
```

### Cause
Another process using port 8000

### Solution

**Find and kill process:**
```bash
lsof -ti:8000 | xargs kill -9
```

**Or use different port:**
```bash
uvicorn app:app --port 8001
```

---

## Issue: Google Calendar Not Working

### Error Message
```
✗ credentials.json not found
```

### Cause
Google Calendar API not configured

### Solution

**Option 1: Use URL Method (No Setup)**
- Click "Add to Google Calendar" button
- Opens pre-filled form
- No authentication required

**Option 2: Setup API (Full Integration)**
See [README.md](README.md) Google Calendar Setup section

---

## Issue: Image Upload Fails

### Error Message
```
No text could be extracted from the image
```

### Cause
- Image quality too low
- No readable text in image
- Wrong image format

### Solution

1. **Use clear, high-quality images**
2. **Supported formats:** PNG, JPG, JPEG
3. **Ensure text is readable**
4. **Try different image**

---

## Issue: Time Parsing Incorrect

### Symptom
Times not parsed correctly from image

### Solution

**Manual Edit:**
1. Click "Edit" button
2. Update time field
3. Click "Save Changes"

**Supported Formats:**
- `10AM-4PM`
- `6:00 PM - 11:00 PM`
- `9:30AM-5:30PM`

---

## Testing & Verification

### Quick Test
```bash
python3 test_app.py
```

### Full Test
```bash
# 1. Start server
python3 app.py

# 2. Open browser
open http://localhost:8000

# 3. Upload test image
# 4. Verify extraction
# 5. Test buttons
```

---

## Debug Mode

### Enable Verbose Logging
```bash
uvicorn app:app --reload --log-level debug
```

### Check Logs
- Server logs appear in terminal
- Look for error stack traces
- Check file paths

---

## Reset & Clean Start

### Remove Cached Files
```bash
rm -rf __pycache__
rm -rf uploads/*
rm token.json  # Regenerates on next run
```

### Reinstall Dependencies
```bash
pip uninstall -y -r requirements.txt
pip install -r requirements.txt
```

### Fresh Start
```bash
# 1. Kill existing processes
lsof -ti:8000 | xargs kill -9

# 2. Clean cache
rm -rf __pycache__

# 3. Verify templates
ls templates/

# 4. Start fresh
python3 app.py
```

---

## Environment Check

### System Info
```bash
python3 --version  # Should be 3.7+
tesseract --version
which python3
pwd  # Should be in FamPilot directory
```

### Dependencies Check
```bash
pip list | grep -E "fastapi|uvicorn|anthropic|pytesseract|Pillow"
```

### Templates Check
```bash
ls -la templates/
# Should show: index.html, result.html, confirmed.html
```

---

## Performance Issues

### Slow Image Processing
- **Cause:** Large image files
- **Solution:** Resize images before upload (< 2MB recommended)

### Slow API Response
- **Cause:** Claude API latency
- **Solution:** Normal, typically 3-5 seconds

### Memory Usage
- **Cause:** Multiple uploads without cleanup
- **Solution:** Restart server periodically

---

## Browser Issues

### Drag & Drop Not Working
- **Cause:** Browser compatibility
- **Solution:** Click upload button instead

### Calendar Link Not Opening
- **Cause:** Pop-up blocker
- **Solution:** Allow pop-ups for localhost

### Styling Issues
- **Cause:** Browser cache
- **Solution:** Hard refresh (Cmd+Shift+R or Ctrl+Shift+F5)

---

## Getting Help

### 1. Check Logs
```bash
# Start with debug logging
uvicorn app:app --reload --log-level debug
```

### 2. Run Test Script
```bash
python3 test_app.py
```

### 3. Verify Installation
```bash
./run_web.sh  # Uses built-in checks
```

### 4. Check Documentation
- [README.md](README.md) - Setup guide
- [WEB_README.md](WEB_README.md) - Web interface docs
- [GOOGLE_CALENDAR_URL.md](GOOGLE_CALENDAR_URL.md) - Calendar integration

---

## Still Having Issues?

If problems persist:

1. **Capture Error:**
   ```bash
   python3 app.py 2>&1 | tee error.log
   ```

2. **Check Versions:**
   ```bash
   python3 --version
   pip --version
   tesseract --version
   ```

3. **Clean Reinstall:**
   ```bash
   pip uninstall -y -r requirements.txt
   pip install -r requirements.txt
   python3 test_app.py
   ```

4. **Verify Files:**
   ```bash
   ls -la  # Should show app.py, main.py, templates/, etc.
   ```

---

Built with troubleshooting in mind ✨
