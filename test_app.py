#!/usr/bin/env python3
"""
Quick test script to verify app.py configuration
"""

import sys
from pathlib import Path

def test_imports():
    """Test that all imports work"""
    try:
        import app
        print("✓ app.py imports successfully")
        return True
    except Exception as e:
        print(f"✗ Import error: {e}")
        return False

def test_templates():
    """Test that templates directory is configured correctly"""
    try:
        from fastapi.templating import Jinja2Templates
        templates = Jinja2Templates(directory="templates")
        print("✓ Jinja2Templates initialized")

        # Check templates exist
        templates_path = Path("templates")
        if not templates_path.exists():
            print("✗ templates directory does not exist")
            return False

        required_templates = ["index.html", "result.html", "confirmed.html"]
        for template in required_templates:
            template_path = templates_path / template
            if template_path.exists():
                print(f"  ✓ {template} exists")
            else:
                print(f"  ✗ {template} missing")
                return False

        return True
    except Exception as e:
        print(f"✗ Templates error: {e}")
        return False

def test_functions():
    """Test that key functions are available"""
    try:
        from app import parse_time_simple, generate_google_calendar_url
        print("✓ Functions available")

        # Test parse_time_simple
        start, end = parse_time_simple("10AM-4PM", "2024-07-20")
        if start and end:
            print(f"  ✓ parse_time_simple works: {start.hour}:00 - {end.hour}:00")
        else:
            print("  ✗ parse_time_simple failed")
            return False

        # Test generate_google_calendar_url
        event = {
            "title": "Test Event",
            "start_date": "2024-07-20",
            "time": "10AM-4PM",
            "location": "Test Location"
        }
        url = generate_google_calendar_url(event)
        if url and "calendar.google.com" in url:
            print(f"  ✓ generate_google_calendar_url works")
        else:
            print("  ✗ generate_google_calendar_url failed")
            return False

        return True
    except Exception as e:
        print(f"✗ Functions error: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_dependencies():
    """Test that all required packages are installed"""
    try:
        import fastapi
        print("✓ FastAPI installed")

        import uvicorn
        print("✓ Uvicorn installed")

        import anthropic
        print("✓ Anthropic installed")

        import pytesseract
        print("✓ pytesseract installed")

        from PIL import Image
        print("✓ Pillow installed")

        return True
    except ImportError as e:
        print(f"✗ Missing dependency: {e}")
        return False

def main():
    """Run all tests"""
    print("=" * 50)
    print("FamPilot App Configuration Test")
    print("=" * 50)
    print()

    tests = [
        ("Dependencies", test_dependencies),
        ("Imports", test_imports),
        ("Templates", test_templates),
        ("Functions", test_functions),
    ]

    results = []
    for name, test_func in tests:
        print(f"\n{name}:")
        print("-" * 50)
        result = test_func()
        results.append((name, result))

    print("\n" + "=" * 50)
    print("Summary:")
    print("=" * 50)

    all_passed = True
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")
        if not result:
            all_passed = False

    print()
    if all_passed:
        print("🎉 All tests passed! Ready to run the app.")
        print("\nRun: python3 app.py")
    else:
        print("⚠️  Some tests failed. Fix issues before running.")
        sys.exit(1)

if __name__ == "__main__":
    main()
