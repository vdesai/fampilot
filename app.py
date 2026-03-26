#!/usr/bin/env python3
"""
FamPilot Web Interface
FastAPI backend for event extraction from images
"""

import asyncio
import os
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Tuple
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db

# Import functions from main.py
from main import (
    extract_text_from_image,
    classify_and_extract,
    classify_and_extract_from_image,
    classify_and_extract_multi,
    classify_and_extract_multi_from_image,
    authenticate_google_calendar,
    create_calendar_event,
    GOOGLE_CALENDAR_AVAILABLE,
    TESSERACT_AVAILABLE
)

async def _reminder_checker():
    """Background task: poll every 60 s, log due reminders to stdout."""
    while True:
        await asyncio.sleep(60)
        try:
            for row in db.get_due_reminders():
                print(
                    f"[🔔 REMINDER] {(row['type'] or 'item').upper()}: "
                    f"{row['title']} — was due at {row['reminder_time']}",
                    flush=True,
                )
                db.mark_reminder_sent(row["id"])
        except Exception as exc:
            print(f"[REMINDER ERROR] {exc}", flush=True)


@asynccontextmanager
async def lifespan(app: "FastAPI"):
    task = asyncio.create_task(_reminder_checker())
    yield
    task.cancel()


# Initialize FastAPI app
app = FastAPI(title="FamPilot Event Assistant", lifespan=lifespan)

# Setup templates
templates = Jinja2Templates(directory="templates")

# Temporary storage for results (in-memory, resets on restart)
items_store = {}

# Batch storage for multi-item extraction (in-memory, keyed by batch_id)
batch_store: dict = {}


def _flat_to_result(flat: dict) -> dict:
    """Convert flat multi-extraction item to legacy {type, confidence, reasoning, data} format."""
    rtype = flat.get("type")
    if rtype == "event":
        data = {k: flat.get(k) for k in ("title", "start_date", "end_date", "time", "location", "notes")}
    elif rtype == "task":
        data = {
            "title":    flat.get("title"),
            "due_date": flat.get("start_date"),
            "priority": flat.get("priority"),
            "notes":    flat.get("notes"),
        }
    else:
        data = {
            "title":     flat.get("title"),
            "remind_at": flat.get("remind_at"),
            "notes":     flat.get("notes"),
        }
    return {
        "type":       rtype,
        "confidence": flat.get("confidence", 1.0),
        "reasoning":  flat.get("reasoning", ""),
        "data":       data,
    }


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
    """Render the home page with upload form and upcoming items."""
    from datetime import date
    return templates.TemplateResponse(request, "index.html", {
        "request":            request,
        "nav_page":           "home",
        "upcoming":           db.get_upcoming_items(),
        "today_str":          date.today().isoformat(),
        "triggered_reminders": db.get_recent_reminders(),
    })


@app.post("/dismiss-reminder/{item_id}")
async def dismiss_reminder(item_id: str):
    """Dismiss a triggered reminder banner."""
    db.dismiss_reminder(item_id)
    return RedirectResponse(url="/", status_code=303)


@app.post("/upload")
async def upload_image(request: Request, file: UploadFile = File(...)):
    """Handle image upload, classify content, and extract structured data."""
    file_path = None
    try:
        upload_dir = Path("uploads")
        upload_dir.mkdir(exist_ok=True)
        file_path = upload_dir / file.filename

        with open(file_path, "wb") as f:
            f.write(await file.read())

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return templates.TemplateResponse(request, "result.html", {
                "request": request,
                "error": "ANTHROPIC_API_KEY not set."
            })

        try:
            items = classify_and_extract_multi_from_image(str(file_path), api_key)
        except Exception as e:
            return templates.TemplateResponse(request, "result.html", {
                "request": request,
                "error": f"Failed to analyze image: {str(e)}"
            })
        finally:
            if file_path and file_path.exists():
                file_path.unlink()

        if len(items) == 1:
            item_id = str(uuid4())
            flat = items[0]
            db.save_flat_item(item_id, flat, image_path=file.filename)
            result = _flat_to_result(flat)
            return _render_result(request, result, item_id, image_path=file.filename, skip_db=True)

        batch_id = str(uuid4())
        batch_store[batch_id] = {"items": items, "source": file.filename, "is_image": True}
        return RedirectResponse(url=f"/review/{batch_id}", status_code=303)

    except Exception as e:
        if file_path and file_path.exists():
            file_path.unlink()
        return templates.TemplateResponse(request, "result.html", {
            "request": request,
            "error": f"Error processing image: {str(e)}"
        })


@app.post("/process-text")
async def process_text(request: Request, text: str = Form(...)):
    """Handle text input, classify content, and extract structured data."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return templates.TemplateResponse(request, "result.html", {
            "request": request,
            "error": "ANTHROPIC_API_KEY not set."
        })

    if not text.strip():
        return templates.TemplateResponse(request, "result.html", {
            "request": request,
            "error": "Please enter some text to analyze."
        })

    try:
        items = classify_and_extract_multi(text.strip(), api_key)
    except Exception as e:
        return templates.TemplateResponse(request, "result.html", {
            "request": request,
            "error": f"Failed to analyze text: {str(e)}"
        })

    if len(items) == 1:
        item_id = str(uuid4())
        flat = items[0]
        db.save_flat_item(item_id, flat, source_text=text)
        result = _flat_to_result(flat)
        return _render_result(request, result, item_id, source_text=text, skip_db=True)

    batch_id = str(uuid4())
    batch_store[batch_id] = {"items": items, "source": text, "is_image": False}
    return RedirectResponse(url=f"/review/{batch_id}", status_code=303)


def _result_to_calendar_data(result: Dict) -> Optional[Dict]:
    """Convert any result type into a calendar-compatible data dict, or None if no date available."""
    rtype = result.get("type")
    data = result.get("data", {})

    if rtype == "event":
        return data

    if rtype == "task":
        due = data.get("due_date")
        if not due:
            return None
        notes = data.get("notes") or ""
        priority = data.get("priority") or "medium"
        return {
            "title": data.get("title", "Task"),
            "start_date": due,
            "end_date": None,
            "time": "9:00 AM",
            "location": None,
            "_description": f"Priority: {priority}" + (f"\n{notes}" if notes else ""),
        }

    if rtype == "reminder":
        remind_at = data.get("remind_at") or ""
        # Try to extract a date from remind_at; fall back to today
        from datetime import date
        remind_date = date.today().strftime("%Y-%m-%d")
        remind_time = "8:00 AM"

        # Simple heuristic: if remind_at contains a date-like string already parsed, use it
        import re
        date_match = re.search(r'\d{4}-\d{2}-\d{2}', remind_at)
        if date_match:
            remind_date = date_match.group()
        time_match = re.search(r'\d{1,2}(?::\d{2})?\s*(?:AM|PM|am|pm)', remind_at)
        if time_match:
            remind_time = time_match.group()

        notes = data.get("notes") or ""
        return {
            "title": data.get("title", "Reminder"),
            "start_date": remind_date,
            "end_date": None,
            "time": remind_time,
            "location": None,
            "_description": f"Reminder: {remind_at}" + (f"\n{notes}" if notes else ""),
        }

    return None


CONFIDENCE_THRESHOLD = 0.80


def _render_result(request: Request, result: Dict, item_id: str,
                   source_text: Optional[str] = None,
                   image_path: Optional[str] = None,
                   message: Optional[str] = None,
                   skip_db: bool = False):
    """Persist result, store in memory, build context, render result.html."""
    items_store[item_id] = result
    if not skip_db:
        db.save_item(item_id, result, source_text=source_text, image_path=image_path)

    context = {
        "request": request,
        "result": result,
        "item_id": item_id,
        "low_confidence": result.get("confidence", 1.0) < CONFIDENCE_THRESHOLD,
        "message": message,
    }

    cal_data = _result_to_calendar_data(result)
    if cal_data:
        context["calendar_url"] = generate_google_calendar_url(cal_data)

    return templates.TemplateResponse(request, "result.html", context)


@app.post("/reclassify/{item_id}")
async def reclassify(request: Request, item_id: str, forced_type: str = Form(...)):
    """User overrides the detected type. Keep existing data, swap the type."""
    item = items_store.get(item_id)
    if not item:
        return templates.TemplateResponse(request, "result.html", {
            "request": request,
            "error": "Session expired. Please upload again."
        })

    updated = {
        **item,
        "type": forced_type,
        "confidence": 1.0,
        "reasoning": f"Manually set to '{forced_type}' by user.",
    }
    items_store[item_id] = updated
    db.update_type(item_id, forced_type)

    context = {
        "request": request,
        "result": updated,
        "item_id": item_id,
        "low_confidence": False,
        "message": f"Type changed to {forced_type}.",
    }

    cal_data = _result_to_calendar_data(updated)
    if cal_data:
        context["calendar_url"] = generate_google_calendar_url(cal_data)

    return templates.TemplateResponse(request, "result.html", context)


@app.post("/edit/{item_id}")
async def edit_event(
    request: Request,
    item_id: str,
    title: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(None),
    time: str = Form(...),
    location: str = Form(None)
):
    """Handle event field editing (events only)."""
    existing = items_store.get(item_id, {})
    updated_data = {
        "title": title,
        "start_date": start_date,
        "end_date": end_date if end_date else None,
        "time": time,
        "location": location if location else None
    }
    result = {**existing, "data": updated_data}
    items_store[item_id] = result
    db.update_event_data(item_id, updated_data)

    calendar_url = generate_google_calendar_url(updated_data)

    return templates.TemplateResponse(request, "result.html", {
        "request": request,
        "result": result,
        "item_id": item_id,
        "message": "Event updated successfully!",
        "calendar_url": calendar_url
    })


@app.post("/confirm/{item_id}")
async def confirm_event(request: Request, item_id: str):
    """Confirm event and add to Google Calendar."""
    item = items_store.get(item_id)

    if not item:
        return templates.TemplateResponse(request, "result.html", {
            "request": request,
            "error": "Item not found. Please upload again."
        })

    event_data = item.get("data", {})
    calendar_service = None
    calendar_link = None

    if GOOGLE_CALENDAR_AVAILABLE:
        calendar_service = authenticate_google_calendar()
        if calendar_service:
            success = create_calendar_event(event_data, calendar_service)
            if success:
                calendar_link = "Event added to Google Calendar successfully!"

    calendar_url = generate_google_calendar_url(event_data)

    return templates.TemplateResponse(request, "confirmed.html", {
        "request": request,
        "event": event_data,
        "calendar_added": calendar_service is not None,
        "calendar_link": calendar_link,
        "calendar_url": calendar_url
    })


@app.post("/cancel")
async def cancel_event(request: Request):
    return RedirectResponse(url="/", status_code=303)


@app.get("/review/{batch_id}", response_class=HTMLResponse)
async def review_batch(request: Request, batch_id: str):
    """Show all extracted items for the user to review before saving."""
    batch = batch_store.get(batch_id)
    if not batch:
        return templates.TemplateResponse(request, "result.html", {
            "request": request,
            "error": "Review session expired. Please upload again.",
        })
    return templates.TemplateResponse(request, "review.html", {
        "request": request,
        "batch_id": batch_id,
        "items": list(enumerate(batch["items"])),
        "total": len(batch["items"]),
        "nav_page": None,
    })


@app.post("/review/{batch_id}/save")
async def save_batch(request: Request, batch_id: str):
    """Save selected (and potentially edited) items from the review page."""
    batch = batch_store.get(batch_id)
    source_text = batch.get("source") if batch and not batch.get("is_image") else None
    image_path  = batch.get("source") if batch and batch.get("is_image")     else None

    form = await request.form()
    item_count = int(form.get("item_count", 0))

    for i in range(item_count):
        if not form.get(f"include_{i}"):
            continue
        flat = {
            "type":       form.get(f"type_{i}"),
            "confidence": float(form.get(f"confidence_{i}", 0.9)),
            "reasoning":  form.get(f"reasoning_{i}", ""),
            "title":      form.get(f"title_{i}") or None,
            "start_date": form.get(f"start_date_{i}") or None,
            "end_date":   form.get(f"end_date_{i}") or None,
            "time":       form.get(f"time_{i}") or None,
            "location":   form.get(f"location_{i}") or None,
            "notes":      form.get(f"notes_{i}") or None,
            "priority":   form.get(f"priority_{i}") or None,
            "remind_at":  form.get(f"remind_at_{i}") or None,
        }
        db.save_flat_item(str(uuid4()), flat, source_text=source_text, image_path=image_path)

    batch_store.pop(batch_id, None)
    return RedirectResponse(url="/history", status_code=303)


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    """Show all analyzed items, newest first."""
    rows = db.get_history(limit=100)
    return templates.TemplateResponse(request, "history.html", {
        "request": request,
        "items": rows,
        "nav_page": "history",
    })


@app.get("/history/{item_id}", response_class=HTMLResponse)
async def history_detail(request: Request, item_id: str):
    """Load a past item from DB and render it with the standard result view."""
    row = db.get_item(item_id)
    if not row:
        return templates.TemplateResponse(request, "result.html", {
            "request": request,
            "error": "Item not found in history.",
        })

    result = db.row_to_result(row)
    items_store[item_id] = result

    context = {
        "request": request,
        "result": result,
        "item_id": item_id,
        "low_confidence": False,
        "from_history": True,       # shows Edit + Delete buttons
    }
    cal_data = _result_to_calendar_data(result)
    if cal_data:
        context["calendar_url"] = generate_google_calendar_url(cal_data)

    return templates.TemplateResponse(request, "result.html", context)


@app.get("/history/{item_id}/edit", response_class=HTMLResponse)
async def edit_item_form(request: Request, item_id: str):
    """Show the edit form for a saved history item."""
    row = db.get_item(item_id)
    if not row:
        return RedirectResponse(url="/history", status_code=303)
    return templates.TemplateResponse(request, "edit.html", {
        "request": request,
        "row": row,
        "item_id": item_id,
        "nav_page": "history",
    })


@app.post("/history/{item_id}/update")
async def update_item(
    request: Request,
    item_id: str,
    title: str = Form(""),
    start_date: str = Form(None),
    end_date: str = Form(None),
    time: str = Form(None),
    location: str = Form(None),
    notes: str = Form(None),
    priority: str = Form(None),
    remind_at: str = Form(None),
    reminder_time: str = Form(None),
):
    """Persist edits to a saved item and redirect to its detail page."""
    row = db.get_item(item_id)
    if not row:
        return RedirectResponse(url="/history", status_code=303)

    # Convert datetime-local value (YYYY-MM-DDTHH:MM) to UTC ISO string
    reminder_time_iso = None
    if reminder_time:
        try:
            reminder_time_iso = datetime.fromisoformat(reminder_time).isoformat()
        except ValueError:
            reminder_time_iso = None

    fields = {
        "title":         title or None,
        "start_date":    start_date or None,
        "end_date":      end_date or None,
        "time":          time or None,
        "location":      location or None,
        "notes":         notes or None,
        "priority":      priority or None,
        "remind_at":     remind_at or None,
        "reminder_time": reminder_time_iso,
    }
    db.update_item(item_id, row["type"], fields)
    # Also update in-memory store if present
    if item_id in items_store:
        items_store.pop(item_id)

    return RedirectResponse(url=f"/history/{item_id}", status_code=303)


@app.post("/history/{item_id}/delete")
async def delete_item(request: Request, item_id: str):
    """Delete a saved item and return to history."""
    db.delete_item(item_id)
    items_store.pop(item_id, None)
    return RedirectResponse(url="/history", status_code=303)


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
