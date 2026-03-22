#!/usr/bin/env bash
# Build script for Render deployment
# Installs Tesseract OCR and Python dependencies

set -o errexit  # Exit on error

echo "======================================"
echo "FamPilot Build Script"
echo "======================================"

# Update package list
echo "Updating package list..."
apt-get update

# Install Tesseract OCR
echo "Installing Tesseract OCR..."
apt-get install -y tesseract-ocr

# Verify Tesseract installation
echo "Verifying Tesseract installation..."
tesseract --version

# Install Python dependencies
echo "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "======================================"
echo "Build completed successfully!"
echo "======================================"
