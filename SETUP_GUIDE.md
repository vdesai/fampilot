# FamPilot Setup Guide

Quick setup guide for getting FamPilot Event Assistant running.

## Quick Start (5 minutes)

### 1. Install System Dependencies
```bash
# macOS
brew install tesseract

# Ubuntu/Debian
sudo apt-get install tesseract-ocr
```

### 2. Install Python Packages
```bash
cd FamPilot
pip install -r requirements.txt
```

### 3. Set Anthropic API Key
```bash
export ANTHROPIC_API_KEY='your-api-key-here'
```

### 4. Test Basic Functionality (No Calendar)
```bash
python3 main.py sample_image.png
```

The assistant will work without Google Calendar, showing a warning but processing normally.

---

## Google Calendar Setup (Optional - 10 minutes)

### Overview
To automatically add events to Google Calendar, you need OAuth credentials from Google Cloud.

### Step-by-Step

#### 1. Create Google Cloud Project (2 min)
1. Visit [Google Cloud Console](https://console.cloud.google.com/)
2. Click **Select a project** → **New Project**
3. Name: `FamPilot` (or any name)
4. Click **Create**

#### 2. Enable Calendar API (1 min)
1. In your project, click **☰ Menu** → **APIs & Services** → **Library**
2. Search: `Google Calendar API`
3. Click the result and press **Enable**

#### 3. Configure OAuth Consent Screen (3 min)
1. Go to **APIs & Services** → **OAuth consent screen**
2. Select **External** user type → **Create**
3. Fill in:
   - App name: `FamPilot Event Assistant`
   - User support email: Your email
   - Developer email: Your email
4. Click **Save and Continue** (skip scopes)
5. Add yourself as a test user on the Test Users page
6. Click **Save and Continue**

#### 4. Create OAuth Credentials (2 min)
1. Go to **APIs & Services** → **Credentials**
2. Click **+ CREATE CREDENTIALS** → **OAuth client ID**
3. Application type: **Desktop app**
4. Name: `FamPilot Desktop`
5. Click **Create**
6. Click **Download JSON** (download icon)
7. Rename downloaded file to `credentials.json`
8. Move to FamPilot folder:
   ```bash
   mv ~/Downloads/client_secret_*.json ~/Documents/FamPilot/credentials.json
   ```

#### 5. First Run Authentication (2 min)
```bash
python3 main.py sample_image.png
```

The program will:
1. Open your browser automatically
2. Ask you to choose your Google account
3. Show a warning "Google hasn't verified this app" - click **Continue**
4. Request calendar permissions - click **Continue**
5. Create `token.json` for future use

Done! Future runs won't need browser authentication.

---

## Verification Checklist

- [ ] Tesseract installed: `tesseract --version`
- [ ] Python packages installed: `pip list | grep anthropic`
- [ ] Anthropic API key set: `echo $ANTHROPIC_API_KEY`
- [ ] credentials.json in project folder (for calendar)
- [ ] Successfully ran first authentication (for calendar)

---

## File Structure

After setup, your project should have:
```
FamPilot/
├── main.py                 # Main program
├── requirements.txt        # Dependencies
├── README.md              # Documentation
├── SETUP_GUIDE.md         # This file
├── .gitignore             # Git ignore rules
├── credentials.json       # Google OAuth credentials (DON'T COMMIT)
└── token.json            # Auto-generated token (DON'T COMMIT)
```

---

## Troubleshooting

### "command not found: python"
Use `python3` instead of `python`

### "pytesseract is not installed"
```bash
pip install -r requirements.txt
```

### "tesseract is not installed"
Install Tesseract OCR for your OS (see Step 1)

### "credentials.json not found"
Google Calendar won't work, but basic functionality still available.
Follow steps 1-4 above to enable calendar integration.

### "This app isn't verified" (Google warning)
This is normal for personal apps. Click **Advanced** → **Go to FamPilot (unsafe)**
This appears because the app hasn't gone through Google's verification process.

### Browser doesn't open for authentication
Copy the URL from terminal and paste in browser manually.

---

## Security Notes

- Never commit `credentials.json` or `token.json` to git
- Never share your API keys
- Both files are already in `.gitignore`
- Rotate keys if accidentally exposed

---

## Next Steps

Once setup is complete:
1. Test with a real event flyer image
2. Confirm the event details
3. Check your Google Calendar to see the new event
4. Try editing fields before confirming

Enjoy! 🎉
