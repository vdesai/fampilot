#!/usr/bin/env python3
"""
Test the JSON cleaning function to ensure it handles various response formats
"""

import json
import re


def clean_json_response(response: str) -> str:
    """
    Clean Claude API response to extract valid JSON.
    Removes markdown code blocks and extra whitespace.
    """
    # Remove markdown code blocks (```json ... ``` or ``` ... ```)
    response = re.sub(r'^```json\s*\n', '', response, flags=re.MULTILINE)
    response = re.sub(r'^```\s*\n', '', response, flags=re.MULTILINE)
    response = re.sub(r'\n```\s*$', '', response, flags=re.MULTILINE)

    # Strip leading/trailing whitespace
    response = response.strip()

    return response


def test_cleaning():
    """Test various response formats"""

    # Test case 1: JSON with ```json code blocks
    test1 = """```json
{
  "title": "Summer Music Festival",
  "date": "2024-07-15",
  "time": "6:00 PM",
  "location": "Central Park"
}
```"""

    # Test case 2: JSON with ``` code blocks (no json)
    test2 = """```
{
  "title": "Winter Concert",
  "date": "2024-12-20",
  "time": "7:00 PM",
  "location": "City Hall"
}
```"""

    # Test case 3: Raw JSON (no code blocks)
    test3 = """{
  "title": "Spring Gala",
  "date": "2024-03-15",
  "time": "8:00 PM",
  "location": "Grand Hotel"
}"""

    # Test case 4: JSON with extra whitespace
    test4 = """

{
  "title": "Fall Festival",
  "date": "2024-10-31",
  "time": "5:00 PM",
  "location": "Town Square"
}

"""

    test_cases = [
        ("JSON with ```json blocks", test1),
        ("JSON with ``` blocks", test2),
        ("Raw JSON", test3),
        ("JSON with whitespace", test4)
    ]

    print("Testing JSON cleaning function...\n")
    all_passed = True

    for name, test_input in test_cases:
        try:
            cleaned = clean_json_response(test_input)
            parsed = json.loads(cleaned)
            print(f"✅ PASS: {name}")
            print(f"   Parsed: {parsed['title']}")
        except json.JSONDecodeError as e:
            print(f"❌ FAIL: {name}")
            print(f"   Error: {e}")
            print(f"   Cleaned output: {repr(cleaned)}")
            all_passed = False
        print()

    if all_passed:
        print("🎉 All tests passed!")
    else:
        print("⚠️  Some tests failed")

    return all_passed


if __name__ == "__main__":
    test_cleaning()
