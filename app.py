#!/usr/bin/env python3
"""
FamPilot Web Interface
FastAPI backend for event extraction from images
"""

import os
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Tuple
from urllib.parse import quote

from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Import functions from main.py
from main import (
    extract_text_from_image,
    extract_event_details,
    authenticate_google_calendar,
    create_calendar_event,
    GOOGLE_CALENDAR_AVAILABLE
)

# Initialize FastAPI app
app = FastAPI(title="FamPilot Event Assistant")

# Setup templates
templates = Jinja2Templates(directory="templates")

# Temporary storage for events (in-memory, resets on restart)
events_store = {}


def parse_time_simple(time_str: str, date_str: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    """
    Simple time parser for Google Calendar URL generation.

    Args:
        time_str: Time string like "10AM-4PM" or "6:00 PM - 11:00 PM"
        date_str: Date string in ISO format (YYYY-MM-DD)

    Returns:
        Tuple of (start_datetime, end_datetime) or (None, None) if parsing fails
    """
    if not time_str or not date_str:
        return None, None

    try:
        event_date = datetime.strptime(date_str, '%Y-%m-%d')
        time_str = time_str.strip().upper().replace(' ', '')

        # Try to parse time range
        patterns = [
            r'(\d{1,2}):?(\d{0,2})(AM|PM)?-(\d{1,2}):?(\d{0,2})(AM|PM)?',
            r'(\d{1,2})(AM|PM)-(\d{1,2})(AM|PM)'
        ]

        for pattern in patterns:
            match = re.match(pattern, time_str)
            if match:
                groups = match.groups()

                # Start time
                start_hour = int(groups[0])
                start_min = int(groups[1]) if groups[1] else 0
                start_period = groups[2] if groups[2] else 'AM'

                if start_period == 'PM' and start_hour != 12:
                    start_hour += 12
                elif start_period == 'AM' and start_hour == 12:
                    start_hour = 0

                start_time = event_date.replace(hour=start_hour, minute=start_min, second=0)

                # End time
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

                if end_period == 'PM' and end_hour != 12:
                    end_hour += 12
                elif end_period == 'AM' and end_hour == 12:
                    end_hour = 0

                end_time = event_date.replace(hour=end_hour, minute=end_min, second=0)
                return start_time, end_time

        # Default: 9 AM - 5 PM
        start_time = event_date.replace(hour=9, minute=0, second=0)
        end_time = event_date.replace(hour=17, minute=0, second=0)
        return start_time, end_time

    except Exception:
        return None, None


def generate_google_calendar_url(event: Dict[str, Optional[str]]) -> str:
    """
    Generate a Google Calendar URL with pre-filled event details.
    This method doesn't require authentication.

    Args:
        event: Dictionary containing event details

    Returns:
        Google Calendar URL string
    """
    base_url = "https://calendar.google.com/calendar/render"

    # Get event details
    title = event.get('title') or 'Event'
    start_date = event.get('start_date') or event.get('date')
    end_date = event.get('end_date')
    time_str = event.get('time')
    location = event.get('location') or ''

    # Parse dates and times
    if start_date:
        start_dt, end_dt = parse_time_simple(time_str, start_date)

        if not start_dt:
            # Fallback: all-day event
            try:
                date_obj = datetime.strptime(start_date, '%Y-%m-%d')
                start_dt = date_obj.replace(hour=9, minute=0, second=0)
                end_dt = date_obj.replace(hour=17, minute=0, second=0)
            except:
                return ""

        # Format dates for Google Calendar (YYYYMMDDTHHMMSSZ)
        start_formatted = start_dt.strftime('%Y%m%dT%H%M%S')
        end_formatted = end_dt.strftime('%Y%m%dT%H%M%S')
        dates = f"{start_formatted}/{end_formatted}"
    else:
        return ""

    # Build description
    description_parts = ["Event extracted by FamPilot"]
    if time_str:
        description_parts.append(f"Time: {time_str}")
    if end_date:
        description_parts.append(f"Multi-day event: {start_date} to {end_date}")
    description = "\\n".join(description_parts)

    # Build URL with parameters
    params = [
        "action=TEMPLATE",
        f"text={quote(title)}",
        f"dates={dates}",
        f"details={quote(description)}"
    ]

    if location:
        params.append(f"location={quote(location)}")

    url = f"{base_url}?{'&'.join(params)}"
    return url


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render the home page with upload form."""
    return templates.TemplateResponse(request, "index.html", {"request": request})


@app.post("/upload")
async def upload_image(request: Request, file: UploadFile = File(...)):
    """
    Handle image upload and extract event details.
    """
    try:
        # Save uploaded file temporarily
        upload_dir = Path("uploads")
        upload_dir.mkdir(exist_ok=True)

        file_path = upload_dir / file.filename
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # Step 1: Extract text from image
        text = extract_text_from_image(str(file_path))

        if not text:
            return templates.TemplateResponse(
                request,
                "result.html",
                {
                    "request": request,
                    "error": "No text could be extracted from the image. Please try a different image."
                }
            )

        # Step 2: Extract event details using AI
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return templates.TemplateResponse(
                request,
                "result.html",
                {
                    "request": request,
                    "error": "ANTHROPIC_API_KEY not set. Please configure the API key."
                }
            )

        event_details = extract_event_details(text, api_key)

        # Store event details with a simple ID
        event_id = str(hash(file.filename))
        events_store[event_id] = event_details

        # Generate Google Calendar URL (fallback method)
        calendar_url = generate_google_calendar_url(event_details)

        # Clean up uploaded file
        file_path.unlink()

        # Render result page with event details
        return templates.TemplateResponse(
            request,
            "result.html",
            {
                "request": request,
                "event": event_details,
                "event_id": event_id,
                "extracted_text_length": len(text),
                "calendar_url": calendar_url
            }
        )

    except Exception as e:
        return templates.TemplateResponse(
            request,
            "result.html",
            {
                "request": request,
                "error": f"Error processing image: {str(e)}"
            }
        )


@app.post("/edit/{event_id}")
async def edit_event(
    request: Request,
    event_id: str,
    title: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(None),
    time: str = Form(...),
    location: str = Form(None)
):
    """
    Handle event editing.
    """
    # Update event in store
    event_details = {
        "title": title,
        "start_date": start_date,
        "end_date": end_date if end_date else None,
        "time": time,
        "location": location if location else None
    }
    events_store[event_id] = event_details

    # Regenerate Google Calendar URL with updated details
    calendar_url = generate_google_calendar_url(event_details)

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "request": request,
            "event": event_details,
            "event_id": event_id,
            "message": "Event updated successfully!",
            "calendar_url": calendar_url
        }
    )


@app.post("/confirm/{event_id}")
async def confirm_event(request: Request, event_id: str):
    """
    Confirm event and add to Google Calendar.
    """
    event = events_store.get(event_id)

    if not event:
        return templates.TemplateResponse(
            request,
            "result.html",
            {
                "request": request,
                "error": "Event not found. Please upload the image again."
            }
        )

    # Try to add to Google Calendar
    calendar_service = None
    calendar_link = None

    if GOOGLE_CALENDAR_AVAILABLE:
        calendar_service = authenticate_google_calendar()
        if calendar_service:
            success = create_calendar_event(event, calendar_service)
            if success:
                # Note: We can't easily get the link back from create_calendar_event
                # without modifying it, so we'll just show success
                calendar_link = "Event added to Google Calendar successfully!"

    return templates.TemplateResponse(
        request,
        "confirmed.html",
        {
            "request": request,
            "event": event,
            "calendar_added": calendar_service is not None,
            "calendar_link": calendar_link
        }
    )


@app.post("/cancel")
async def cancel_event(request: Request):
    """
    Cancel event extraction and return to home.
    """
    return RedirectResponse(url="/", status_code=303)


if __name__ == "__main__":
    import uvicorn

    # Check for API key
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("⚠️  Warning: ANTHROPIC_API_KEY not set")
        print("Set it with: export ANTHROPIC_API_KEY='your-key'")

    print("\n" + "=" * 50)
    print("FamPilot Web Interface")
    print("=" * 50)
    print("Starting server at http://localhost:8000")
    print("Press Ctrl+C to stop")
    print("=" * 50 + "\n")

    uvicorn.run(app, host="0.0.0.0", port=8000)
