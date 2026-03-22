# Render Quick Start Guide

Deploy FamPilot to Render in 5 minutes.

---

## Step 1: Prepare Repository

```bash
# Initialize git (if not already)
git init

# Add all files
git add .

# Commit
git commit -m "Ready for Render deployment"

# Push to GitHub
git remote add origin https://github.com/YOUR_USERNAME/fampilot.git
git push -u origin main
```

---

## Step 2: Deploy to Render

1. **Go to Render**: [dashboard.render.com](https://dashboard.render.com)

2. **New Web Service**
   - Click "New +" → "Web Service"
   - Connect GitHub
   - Select your FamPilot repository

3. **Configure**
   ```
   Name:           fampilot
   Environment:    Python 3
   Build Command:  pip install -r requirements.txt
   Start Command:  uvicorn app:app --host 0.0.0.0 --port $PORT
   ```

4. **Add Environment Variable**
   ```
   Key:   ANTHROPIC_API_KEY
   Value: your-api-key-here
   ```

5. **Create Web Service**
   - Click "Create Web Service"
   - Wait ~5 minutes for build

---

## Step 3: Verify

1. **Check Build Logs**
   Look for:
   ```
   Installing Tesseract OCR...
   tesseract 4.1.1
   Build completed successfully!
   ```

2. **Visit Your App**
   ```
   https://fampilot.onrender.com
   ```

3. **Test Upload**
   - Upload an event image
   - Verify OCR extraction works
   - Check event details display

---

## That's It!

Your app is live at: `https://your-app-name.onrender.com`

---

## How It Works

**No Tesseract needed!** The app automatically uses Claude Vision API when Tesseract isn't available.

### Production Mode (Render)
```
Image → Claude Vision API → Event Details
```

### Local Mode (with Tesseract)
```
Image → Tesseract OCR → Claude Text → Event Details
```

**The app detects the environment automatically!**

---

## Troubleshooting

### Build Fails

**Check:** Build command is correct:
```
pip install -r requirements.txt
```

### Slow Image Processing

**Normal:** Vision API takes ~4 seconds vs ~2 seconds with Tesseract

This is expected in production mode.

### API Key Missing

**Add in Render:**
1. Your service → Environment
2. Add `ANTHROPIC_API_KEY`
3. Redeploy

---

## Free Tier Limits

- Sleeps after 15 min inactivity
- 512 MB RAM
- ~30 second cold start
- Perfect for demos!

**Upgrade to Starter ($7/mo) for always-on.**

---

## Updates

Push to GitHub = Auto-deploy:
```bash
git add .
git commit -m "Update"
git push
```

Render rebuilds automatically!

---

**Need help?** See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed guide.
