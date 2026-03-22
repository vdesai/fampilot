#!/bin/bash
# Quick start script for FamPilot web interface

echo "======================================"
echo "FamPilot Web Interface Startup"
echo "======================================"
echo ""

# Check if ANTHROPIC_API_KEY is set
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "⚠️  Warning: ANTHROPIC_API_KEY not set"
    echo "Set it with: export ANTHROPIC_API_KEY='your-key'"
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Check if Tesseract is installed
if ! command -v tesseract &> /dev/null; then
    echo "⚠️  Warning: Tesseract not installed"
    echo "Install with: brew install tesseract (macOS)"
    echo ""
fi

# Create required directories
mkdir -p uploads templates

# Check if dependencies are installed
echo "Checking dependencies..."
python3 -c "import fastapi, uvicorn, anthropic, pytesseract" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "⚠️  Some dependencies missing"
    echo "Installing dependencies..."
    pip install -r requirements.txt
fi

echo ""
echo "✓ Starting FamPilot web server..."
echo ""
echo "Access the app at: http://localhost:8000"
echo "Press Ctrl+C to stop"
echo ""

# Start the server
python3 app.py
