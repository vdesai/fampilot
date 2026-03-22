# Deployment Guide - Render

## Overview

FamPilot can be deployed to Render with automatic Tesseract OCR installation.

---

## Prerequisites

1. **Render Account** - Sign up at [render.com](https://render.com)
2. **GitHub Repository** - Push your code to GitHub
3. **Anthropic API Key** - Get from [console.anthropic.com](https://console.anthropic.com)

---

## Quick Deploy

### Option 1: Deploy from GitHub (Recommended)

1. **Push to GitHub**
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin https://github.com/YOUR_USERNAME/fampilot.git
   git push -u origin main
   ```

2. **Connect to Render**
   - Go to [dashboard.render.com](https://dashboard.render.com)
   - Click "New +" → "Web Service"
   - Connect your GitHub repository
   - Select the FamPilot repository

3. **Configure Service**
   - **Name:** `fampilot` (or your choice)
   - **Environment:** `Python 3`
   - **Build Command:** `./build.sh`
   - **Start Command:** `uvicorn app:app --host 0.0.0.0 --port $PORT`
   - **Instance Type:** Free or Starter

4. **Add Environment Variables**
   - Click "Environment"
   - Add: `ANTHROPIC_API_KEY` = `your-api-key`

5. **Deploy**
   - Click "Create Web Service"
   - Wait for build to complete (~5 minutes)
   - Access at: `https://fampilot.onrender.com`

### Option 2: Deploy with render.yaml

1. **Use Infrastructure as Code**
   ```bash
   # render.yaml is already in the repository
   git push
   ```

2. **In Render Dashboard**
   - Click "New +" → "Blueprint"
   - Select your repository
   - Render reads `render.yaml` automatically

3. **Set Environment Variables**
   - Add `ANTHROPIC_API_KEY` in dashboard

---

## Build Process

### build.sh

The build script automatically:

1. Updates apt package list
2. Installs Tesseract OCR
3. Verifies installation
4. Upgrades pip
5. Installs Python dependencies

```bash
#!/usr/bin/env bash
set -o errexit

echo "Installing Tesseract OCR..."
apt-get update
apt-get install -y tesseract-ocr

echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt
```

### Why This Works

- **Render uses Debian/Ubuntu** - `apt-get` works natively
- **Build script runs with root** - Can install system packages
- **Tesseract installed first** - Before Python dependencies
- **Cached between deploys** - Faster subsequent builds

---

## Environment Variables

Required:
```
ANTHROPIC_API_KEY=sk-ant-...
```

Optional (auto-configured by Render):
```
PORT=10000              # Render assigns dynamically
PYTHON_VERSION=3.12.0   # Specified in render.yaml
```

---

## Verification

### Check Build Logs

After deployment, verify in build logs:

```
====================================
FamPilot Build Script
====================================
Updating package list...
Installing Tesseract OCR...
tesseract 4.1.1
Installing Python dependencies...
Successfully installed...
====================================
Build completed successfully!
====================================
```

### Test Deployment

1. **Visit your URL**
   ```
   https://your-app.onrender.com
   ```

2. **Upload test image**
   - Should extract text successfully
   - No Tesseract errors

3. **Check logs**
   ```
   # In Render dashboard
   Logs → View logs
   ```

---

## Deployment Configuration

### render.yaml

```yaml
services:
  - type: web
    name: fampilot
    env: python
    buildCommand: "./build.sh"
    startCommand: "uvicorn app:app --host 0.0.0.0 --port $PORT"
    envVars:
      - key: PYTHON_VERSION
        value: 3.12.0
      - key: ANTHROPIC_API_KEY
        sync: false
```

### Key Points

- **buildCommand**: Runs `build.sh` to install Tesseract
- **startCommand**: Starts Uvicorn on Render's port
- **envVars**:
  - `PYTHON_VERSION`: Specifies Python 3.12
  - `ANTHROPIC_API_KEY`: Must be set in dashboard (sync: false)

---

## Google Calendar Integration

### Without API (URL Method)

Works automatically - no configuration needed.

### With API (Full Integration)

Not recommended for Render free tier:
- OAuth flow requires persistent storage
- `credentials.json` would need to be in repo (security risk)
- `token.json` gets lost on container restarts

**Recommendation**: Use URL method for Render deployments.

---

## File Structure for Deployment

```
FamPilot/
├── app.py              # FastAPI application
├── main.py             # Core logic
├── requirements.txt    # Python dependencies
├── build.sh           # Build script (Tesseract install)
├── render.yaml        # Render configuration
├── templates/         # HTML templates
│   ├── index.html
│   ├── result.html
│   └── confirmed.html
└── uploads/           # Created at runtime
```

---

## Troubleshooting

### Tesseract Not Found

**Error:**
```
pytesseract.pytesseract.TesseractNotFoundError
```

**Solution:**
1. Verify `build.sh` is executable: `chmod +x build.sh`
2. Check build command in Render: `./build.sh`
3. Review build logs for installation errors

### Build Fails

**Error:**
```
bash: ./build.sh: Permission denied
```

**Solution:**
```bash
chmod +x build.sh
git add build.sh
git commit -m "Make build.sh executable"
git push
```

### Port Issues

**Error:**
```
Address already in use
```

**Solution:**
Render sets `$PORT` automatically. Ensure start command uses:
```
uvicorn app:app --host 0.0.0.0 --port $PORT
```

### API Key Not Working

**Error:**
```
ANTHROPIC_API_KEY not set
```

**Solution:**
1. Go to Render dashboard
2. Your service → Environment
3. Add `ANTHROPIC_API_KEY` variable
4. Trigger redeploy

---

## Performance

### Free Tier

- **Startup Time**: ~30 seconds (cold start)
- **Active Time**: Fast after warm-up
- **Sleeps**: After 15 minutes of inactivity
- **Build Time**: ~5 minutes (cached ~2 minutes)

### Paid Tier

- **No Sleep**: Always active
- **Faster**: More resources
- **Auto-scaling**: Handles traffic spikes

---

## Updates & Redeployment

### Automatic Deploys

Render auto-deploys on git push:

```bash
git add .
git commit -m "Update feature"
git push
```

### Manual Redeploy

In Render dashboard:
- Click "Manual Deploy"
- Select branch
- Deploy

### Clear Cache

If build issues persist:
- Click "Settings"
- "Clear build cache"
- Redeploy

---

## Custom Domain

### Add Domain

1. **In Render Dashboard**
   - Your service → Settings
   - Custom Domains → Add
   - Enter: `fampilot.yourdomain.com`

2. **In DNS Provider**
   - Add CNAME record
   - Point to: `your-app.onrender.com`

3. **SSL**
   - Render provides free SSL
   - Auto-renews

---

## Monitoring

### Health Check

Render pings: `https://your-app.onrender.com/`

Returns 200 OK if healthy.

### Logs

View in dashboard:
- Real-time logs
- Filter by level
- Download logs

### Metrics

- CPU usage
- Memory usage
- Request rate
- Response time

---

## Security

### Environment Variables

- Never commit `.env` files
- Use Render's environment variables
- Mark sensitive vars as secret

### API Keys

- Rotate regularly
- Use scoped keys
- Monitor usage

### HTTPS

- Render provides free SSL
- All traffic encrypted
- Auto-renewal

---

## Cost Estimation

### Free Tier

- **Price**: $0/month
- **Instances**: 1
- **RAM**: 512 MB
- **Storage**: 1 GB
- **Bandwidth**: Limited
- **Sleep**: After 15 min inactivity

### Starter Tier ($7/month)

- **Always On**: No sleep
- **RAM**: 512 MB
- **Storage**: 1 GB
- **Better Performance**

### Professional ($25/month)

- **RAM**: 2 GB
- **Auto-scaling**
- **Priority Support**

---

## Alternative Platforms

If Render doesn't work:

### Heroku

Similar process:
```bash
# Add buildpack
heroku buildpacks:add --index 1 https://github.com/heroku/heroku-buildpack-apt
```

Create `Aptfile`:
```
tesseract-ocr
```

### Railway

Auto-detects dependencies:
- Deploy from GitHub
- Nixpacks builds automatically

### Fly.io

Use Dockerfile:
```dockerfile
FROM python:3.12
RUN apt-get update && apt-get install -y tesseract-ocr
```

---

## Production Checklist

Before deploying:

- [ ] `build.sh` is executable
- [ ] `render.yaml` configured
- [ ] Environment variables set
- [ ] Test locally first
- [ ] `.gitignore` updated
- [ ] No secrets in repo
- [ ] Error handling tested
- [ ] Logs configured
- [ ] Health check works

After deploying:

- [ ] Test image upload
- [ ] Verify OCR works
- [ ] Check calendar integration
- [ ] Test edit functionality
- [ ] Monitor logs
- [ ] Set up alerts
- [ ] Configure custom domain (optional)

---

## Support

**Render Docs**: [render.com/docs](https://render.com/docs)

**Status**: [status.render.com](https://status.render.com)

**Community**: [community.render.com](https://community.render.com)

---

Built for easy deployment ✨
