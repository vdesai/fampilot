#!/usr/bin/env python3
"""
FamPilot - Interactive Event Assistant
Extracts event information from images and adds them to Google Calendar
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Tuple

try:
    import pytesseract
    from PIL import Image
except ImportError:
    print("Error: Required packages not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

try:
    from anthropic import Anthropic
except ImportError:
    print("Error: anthropic package not installed. Run: pip install anthropic")
    sys.exit(1)

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from dateutil import parser as date_parser
except ImportError:
    GOOGLE_CALENDAR_AVAILABLE = False
else:
    GOOGLE_CALENDAR_AVAILABLE = True

# Google Calendar API scopes
SCOPES = ['https://www.googleapis.com/auth/calendar']

# Check if Tesseract is available
TESSERACT_AVAILABLE = False
try:
    pytesseract.get_tesseract_version()
    TESSERACT_AVAILABLE = True
except:
    TESSERACT_AVAILABLE = False


# ============================================================================
# OCR EXTRACTION
# ============================================================================

def extract_text_from_image(image_path: str) -> str:
    """
    Extract text from an image using Tesseract OCR.

    Note: This function is only used if Tesseract is installed locally.
    For production/deployment, use extract_event_from_image_vision instead.

    Args:
        image_path: Path to the image file

    Returns:
        Extracted text as string

    Raises:
        FileNotFoundError: If image file doesn't exist
        Exception: If OCR extraction fails or Tesseract not available
    """
    if not TESSERACT_AVAILABLE:
        raise Exception("Tesseract not installed. Use vision-based extraction instead.")

    try:
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img)
        return text.strip()
    except FileNotFoundError:
        raise FileNotFoundError(f"Image file not found: {image_path}")
    except Exception as e:
        raise Exception(f"Error extracting text from image: {str(e)}")


def extract_event_from_image_vision(image_path: str, api_key: str) -> Dict[str, Optional[str]]:
    """
    Extract event details directly from image using Claude Vision API.
    This method works without Tesseract OCR installation.

    Args:
        image_path: Path to the image file
        api_key: Anthropic API key

    Returns:
        Dictionary with extracted event information including:
        - title: Event name
        - start_date: Start date (YYYY-MM-DD)
        - end_date: End date for multi-day events (YYYY-MM-DD) or null
        - time: Original time string
        - location: Venue or null
    """
    import base64
    from pathlib import Path

    # Read and encode image
    image_file = Path(image_path)
    if not image_file.exists():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    with open(image_file, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    # Determine media type
    suffix = image_file.suffix.lower()
    media_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp'
    }
    media_type = media_types.get(suffix, 'image/jpeg')

    client = Anthropic(api_key=api_key)

    prompt = """Analyze this event flyer or invitation image and extract the event details.

Extract the following information and return ONLY raw JSON (no markdown, no code blocks, no explanations):

Required fields:
- title: The event title/name
- start_date: The event start date (in ISO format YYYY-MM-DD)
- end_date: The event end date if it's a multi-day event (YYYY-MM-DD), or null for single-day events
- time: The event time as written (keep original format like "10AM-4PM" or "6:00 PM - 11:00 PM")
- location: The event location/venue

If any field cannot be determined from the image, use null.

For date ranges like "July 20-22" or "Jul 20-22, 2024":
- start_date: "2024-07-20"
- end_date: "2024-07-22"

For single dates like "July 20" or "July 20, 2024":
- start_date: "2024-07-20"
- end_date: null

CRITICAL: Return ONLY the raw JSON object. No markdown, no code blocks, no explanations."""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ],
                }
            ],
        )

        response_text = message.content[0].text
        cleaned_response = clean_json_response(response_text)
        event_info = json.loads(cleaned_response)

        return event_info

    except json.JSONDecodeError as e:
        raise Exception(f"Failed to parse Claude API response as JSON: {str(e)}")
    except Exception as e:
        raise Exception(f"Error calling Claude Vision API: {str(e)}")


# ============================================================================
# AI CLASSIFICATION & EXTRACTION
# ============================================================================

def _classify_prompt() -> str:
    today = datetime.now()
    date_line = f'Today is {today.strftime("%A, %B %d, %Y")} (ISO: {today.strftime("%Y-%m-%d")}).'
    return date_line + """
Use this to resolve relative dates like "this Friday", "next Monday", "tomorrow", or bare month/day references (assume the nearest future occurrence).

Analyze the input and classify it as one of: event, task, or reminder.

Return ONLY raw JSON in this exact format:
{
  "type": "event",
  "confidence": 0.95,
  "reasoning": "brief explanation",
  "data": {}
}

Classification rules:
- event: Something happening at a specific time/place (party, meeting, concert, appointment, flyer, invitation)
- task: Something that needs to be done (to-do, checklist, action item)
- reminder: A note to remember something (take medicine, follow up, check something)

Data fields by type:
- event data: {"title": str, "start_date": "YYYY-MM-DD or null", "end_date": "YYYY-MM-DD or null", "time": "str or null", "location": "str or null"}
- task data: {"title": str, "due_date": "YYYY-MM-DD or null", "priority": "high/medium/low", "notes": "str or null"}
- reminder data: {"title": str, "remind_at": "str or null", "notes": "str or null"}

CRITICAL: Return ONLY the raw JSON object. No markdown, no code blocks."""


def classify_and_extract(text: str, api_key: str) -> Dict:
    """
    Classify text as event/task/reminder and extract structured data.

    Returns:
        {"type": str, "confidence": float, "reasoning": str, "data": dict}
    """
    client = Anthropic(api_key=api_key)

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": f"{_classify_prompt()}\n\nText:\n{text}"}]
    )

    cleaned = clean_json_response(message.content[0].text)
    return json.loads(cleaned)


MAX_IMAGE_BYTES = 4 * 1024 * 1024  # 4MB — stay under Claude's 5MB base64 limit


def _compress_image(image_path: Path) -> tuple[bytes, str]:
    """Return (image_bytes, media_type), compressing/resizing if over MAX_IMAGE_BYTES."""
    from PIL import Image
    import io

    img = Image.open(image_path)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    # Try progressively lower quality until under the limit
    for quality in (85, 70, 55, 40):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= MAX_IMAGE_BYTES:
            return data, "image/jpeg"

    # Still too large — also scale down
    w, h = img.size
    img = img.resize((w // 2, h // 2), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=60, optimize=True)
    return buf.getvalue(), "image/jpeg"


def classify_and_extract_from_image(image_path: str, api_key: str) -> Dict:
    """
    Classify image content and extract structured data using Claude Vision.

    Returns:
        {"type": str, "confidence": float, "reasoning": str, "data": dict}
    """
    import base64

    image_file = Path(image_path)
    if not image_file.exists():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    raw = image_file.read_bytes()
    if len(raw) > MAX_IMAGE_BYTES:
        raw, media_type = _compress_image(image_file)
    else:
        media_types = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp'}
        media_type = media_types.get(image_file.suffix.lower(), 'image/jpeg')

    image_data = base64.standard_b64encode(raw).decode("utf-8")

    client = Anthropic(api_key=api_key)

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_data}},
                {"type": "text", "text": _classify_prompt()}
            ]
        }]
    )

    cleaned = clean_json_response(message.content[0].text)
    return json.loads(cleaned)


# ============================================================================
# AI EVENT EXTRACTION (legacy — kept for Google Calendar integration)
# ============================================================================

def clean_json_response(response: str) -> str:
    """
    Clean Claude API response to extract valid JSON.
    Removes markdown code blocks and extra whitespace.

    Args:
        response: Raw response text from Claude API

    Returns:
        Cleaned JSON string
    """
    # Remove markdown code blocks
    response = re.sub(r'^```json\s*\n', '', response, flags=re.MULTILINE)
    response = re.sub(r'^```\s*\n', '', response, flags=re.MULTILINE)
    response = re.sub(r'\n```\s*$', '', response, flags=re.MULTILINE)
    return response.strip()


def extract_event_details(text: str, api_key: str) -> Dict[str, Optional[str]]:
    """
    Use Claude API to extract structured event information from text.

    Args:
        text: Raw text extracted from image
        api_key: Anthropic API key

    Returns:
        Dictionary with extracted event information including:
        - title: Event name
        - start_date: Start date (YYYY-MM-DD)
        - end_date: End date for multi-day events (YYYY-MM-DD) or null
        - time: Original time string
        - location: Venue or null
    """
    client = Anthropic(api_key=api_key)

    prompt = f"""Analyze the following text extracted from an event flyer or invitation.
Extract the following information and return ONLY raw JSON (no markdown, no code blocks, no explanations):

Required fields:
- title: The event title/name
- start_date: The event start date (in ISO format YYYY-MM-DD)
- end_date: The event end date if it's a multi-day event (YYYY-MM-DD), or null for single-day events
- time: The event time as written (keep original format like "10AM-4PM" or "6:00 PM - 11:00 PM")
- location: The event location/venue

If any field cannot be determined from the text, use null.

For date ranges like "July 20-22" or "Jul 20-22, 2024":
- start_date: "2024-07-20"
- end_date: "2024-07-22"

For single dates like "July 20" or "July 20, 2024":
- start_date: "2024-07-20"
- end_date: null

Text:
{text}

CRITICAL: Return ONLY the raw JSON object. No markdown, no code blocks, no explanations."""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )

        response_text = message.content[0].text
        cleaned_response = clean_json_response(response_text)
        event_info = json.loads(cleaned_response)

        return event_info

    except json.JSONDecodeError as e:
        raise Exception(f"Failed to parse Claude API response as JSON: {str(e)}")
    except Exception as e:
        raise Exception(f"Error calling Claude API: {str(e)}")


# ============================================================================
# USER INTERACTION & CONFIRMATION
# ============================================================================

def display_event_summary(event: Dict[str, Optional[str]]) -> None:
    """
    Display event details in a clean, user-friendly format.

    Args:
        event: Dictionary containing event details
    """
    print("\n" + "=" * 50)
    print("📋 Event Details")
    print("=" * 50)

    print(f"Title:    {event.get('title') or 'Not found'}")

    # Display date(s)
    start_date = event.get('start_date')
    end_date = event.get('end_date')
    if start_date and end_date:
        print(f"Dates:    {start_date} to {end_date}")
    elif start_date:
        print(f"Date:     {start_date}")
    else:
        print(f"Date:     Not found")

    print(f"Time:     {event.get('time') or 'Not found'}")
    print(f"Location: {event.get('location') or 'Not found'}")

    print("=" * 50 + "\n")


def edit_field(event: Dict[str, Optional[str]], field: str) -> None:
    """
    Allow user to edit a specific field interactively.

    Args:
        event: Dictionary containing event details
        field: Field name to edit
    """
    current_value = event.get(field) or "Not set"
    print(f"\nCurrent {field}: {current_value}")
    new_value = input(f"Enter new {field} (or press Enter to keep): ").strip()

    if new_value:
        event[field] = new_value
        print(f"✓ Updated {field}")
    else:
        print(f"↩ Kept current {field}")


def edit_event_fields(event: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
    """
    Allow user to edit individual event fields interactively.

    Args:
        event: Dictionary containing event details

    Returns:
        Updated event dictionary
    """
    print("\n" + "=" * 50)
    print("Edit Mode")
    print("=" * 50)
    print("1. Title")
    print("2. Start Date")
    print("3. End Date")
    print("4. Time")
    print("5. Location")
    print("6. Done editing")
    print("=" * 50)

    while True:
        choice = input("\nEdit field (1-6): ").strip()

        if choice == "1":
            edit_field(event, "title")
        elif choice == "2":
            edit_field(event, "start_date")
        elif choice == "3":
            edit_field(event, "end_date")
        elif choice == "4":
            edit_field(event, "time")
        elif choice == "5":
            edit_field(event, "location")
        elif choice == "6":
            print("\n✓ Finished editing\n")
            break
        else:
            print("Invalid choice. Please enter 1-6.")

    return event


def confirm_event_with_user(event: Dict[str, Optional[str]]) -> bool:
    """
    Interactive confirmation workflow with the user.

    Args:
        event: Dictionary containing event details

    Returns:
        True if event confirmed, False if cancelled
    """
    display_event_summary(event)

    while True:
        response = input("Add to calendar? (yes/edit/no): ").strip().lower()

        if response in ["yes", "y"]:
            return True
        elif response in ["edit", "e"]:
            event = edit_event_fields(event)
            display_event_summary(event)
        elif response in ["no", "n"]:
            return False
        else:
            print("Please enter 'yes', 'edit', or 'no'")


# ============================================================================
# DATE/TIME PARSING
# ============================================================================

def parse_time_range(time_str: str, date_str: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    """
    Parse time range string into start and end datetime objects.

    Args:
        time_str: Time string (e.g., "10AM-4PM", "6:00 PM - 11:00 PM")
        date_str: Date string in ISO format (YYYY-MM-DD)

    Returns:
        Tuple of (start_datetime, end_datetime) or (None, None) if parsing fails
    """
    if not time_str or not date_str:
        return None, None

    try:
        # Parse the date
        event_date = datetime.strptime(date_str, '%Y-%m-%d')

        # Clean up time string
        time_str = time_str.strip().upper().replace(' ', '')

        # Try to parse time range patterns
        time_patterns = [
            r'(\d{1,2}):?(\d{0,2})(AM|PM)?-(\d{1,2}):?(\d{0,2})(AM|PM)?',
            r'(\d{1,2})(AM|PM)-(\d{1,2})(AM|PM)'
        ]

        for pattern in time_patterns:
            match = re.match(pattern, time_str)
            if match:
                groups = match.groups()

                # Extract start time
                start_hour = int(groups[0])
                start_min = int(groups[1]) if groups[1] else 0
                start_period = groups[2] if groups[2] else 'AM'

                # Convert to 24-hour format
                if start_period == 'PM' and start_hour != 12:
                    start_hour += 12
                elif start_period == 'AM' and start_hour == 12:
                    start_hour = 0

                start_time = event_date.replace(hour=start_hour, minute=start_min, second=0)

                # Extract end time
                if len(groups) >= 6:
                    end_hour = int(groups[3])
                    end_min = int(groups[4]) if groups[4] else 0
                    end_period = groups[5] if groups[5] else 'PM'
                elif len(groups) >= 4:
                    end_hour = int(groups[2] if len(groups) == 4 else groups[3])
                    end_min = 0
                    end_period = groups[3] if len(groups) == 4 else groups[5]
                else:
                    end_time = start_time + timedelta(hours=1)
                    return start_time, end_time

                # Convert to 24-hour format
                if end_period == 'PM' and end_hour != 12:
                    end_hour += 12
                elif end_period == 'AM' and end_hour == 12:
                    end_hour = 0

                end_time = event_date.replace(hour=end_hour, minute=end_min, second=0)

                return start_time, end_time

        # If no pattern matches, try parsing as single time
        try:
            start_time = date_parser.parse(f"{date_str} {time_str}")
            end_time = start_time + timedelta(hours=1)
            return start_time, end_time
        except:
            pass

        # Default: 9 AM - 10 AM
        start_time = event_date.replace(hour=9, minute=0, second=0)
        end_time = start_time + timedelta(hours=1)
        return start_time, end_time

    except Exception:
        return None, None


# ============================================================================
# GOOGLE CALENDAR INTEGRATION
# ============================================================================

def authenticate_google_calendar():
    """
    Authenticate with Google Calendar API using OAuth2.

    Returns:
        Google Calendar service object or None if authentication fails
    """
    if not GOOGLE_CALENDAR_AVAILABLE:
        print("✗ Google Calendar packages not installed")
        return None

    creds = None
    token_path = Path('token.json')
    credentials_path = Path('credentials.json')

    if not credentials_path.exists():
        print("✗ credentials.json not found")
        print("  See README.md for setup instructions")
        return None

    # Load existing token
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    # Refresh or create new credentials
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds:
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(credentials_path), SCOPES)
                creds = flow.run_local_server(port=0)
            except Exception as e:
                print(f"✗ Authentication failed: {e}")
                return None

        # Save credentials
        with open(token_path, 'w') as token:
            token.write(creds.to_json())

    try:
        service = build('calendar', 'v3', credentials=creds)
        return service
    except Exception as e:
        print(f"✗ Failed to build calendar service: {e}")
        return None


def create_calendar_event(event: Dict[str, Optional[str]], service) -> bool:
    """
    Create an event in Google Calendar.

    Args:
        event: Dictionary containing event details
        service: Google Calendar service object

    Returns:
        True if event was created successfully, False otherwise
    """
    if not service:
        return False

    try:
        # Use start_date, fall back to 'date' for compatibility
        start_date = event.get('start_date') or event.get('date')
        end_date = event.get('end_date')

        if not start_date:
            print("✗ No date found in event")
            return False

        # Parse times
        start_time, end_time = parse_time_range(event.get('time'), start_date)

        if not start_time or not end_time:
            print("✗ Could not parse time")
            return False

        # Build calendar event
        calendar_event = {
            'summary': event.get('title') or 'Untitled Event',
            'start': {
                'dateTime': start_time.isoformat(),
                'timeZone': 'America/Los_Angeles',
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': 'America/Los_Angeles',
            },
        }

        # Add location if available
        if event.get('location'):
            calendar_event['location'] = event['location']

        # Add description
        description_parts = ["Event extracted from image"]
        if event.get('time'):
            description_parts.append(f"Original time: {event['time']}")
        if end_date:
            description_parts.append(f"Multi-day event: {start_date} to {end_date}")
        calendar_event['description'] = "\n".join(description_parts)

        # Create event
        created_event = service.events().insert(
            calendarId='primary',
            body=calendar_event
        ).execute()

        print(f"\n✓ Event added to Google Calendar")
        print(f"  {created_event.get('htmlLink')}\n")
        return True

    except HttpError as error:
        print(f"✗ Google Calendar API error: {error}")
        return False
    except Exception as e:
        print(f"✗ Error creating calendar event: {e}")
        return False


# ============================================================================
# MAIN WORKFLOW
# ============================================================================

def main():
    """Main workflow orchestrator."""

    # Check arguments
    if len(sys.argv) < 2:
        print("\nFamPilot - Event Assistant")
        print("=" * 50)
        print("Usage: python3 main.py <image_path>")
        print("\nEnvironment variables:")
        print("  ANTHROPIC_API_KEY - Required")
        print("=" * 50 + "\n")
        sys.exit(1)

    image_path = sys.argv[1]

    # Get API key
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("✗ ANTHROPIC_API_KEY environment variable not set")
        sys.exit(1)

    # Validate image
    if not Path(image_path).exists():
        print(f"✗ Image not found: {image_path}")
        sys.exit(1)

    try:
        # Step 1: OCR Extraction
        print(f"\nAnalyzing: {image_path}")
        text = extract_text_from_image(image_path)

        if not text:
            print("✗ No text extracted from image")
            sys.exit(1)

        print(f"✓ Extracted {len(text)} characters\n")

        # Step 2: AI Event Extraction
        print("Processing with AI...")
        event_info = extract_event_details(text, api_key)
        print("✓ Event details extracted\n")

        # Step 3: User Confirmation
        confirmed = confirm_event_with_user(event_info)

        if not confirmed:
            print("✗ Event cancelled\n")
            sys.exit(0)

        # Step 4: Google Calendar Integration (only if confirmed)
        print("\nAuthenticating with Google Calendar...")
        calendar_service = authenticate_google_calendar()

        if calendar_service:
            print("✓ Connected to Google Calendar\n")
            create_calendar_event(event_info, calendar_service)
        else:
            print("\nEvent confirmed but not added to calendar.")
            print("See README.md for Google Calendar setup.\n")

        # Output final JSON
        print("Final event data:")
        print(json.dumps(event_info, indent=2))
        print()

    except KeyboardInterrupt:
        print("\n\n✗ Cancelled by user\n")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Error: {str(e)}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
