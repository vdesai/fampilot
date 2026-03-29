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

from fastapi import FastAPI, UploadFile, File, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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

COOKIE_NAME = "fp_device"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365 * 2  # 2 years


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

# Static files + templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Temporary storage for results (in-memory, resets on restart)
items_store = {}

# Batch storage for multi-item extraction (in-memory, keyed by batch_id)
batch_store: dict = {}


# ── Auth helpers ──

def _get_auth(request: Request) -> Optional[dict]:
    """Resolve device cookie → {device_id, member_id, family_id, display_name, role, family_name}."""
    device_id = request.cookies.get(COOKIE_NAME)
    if not device_id:
        return None
    info = db.resolve_device(device_id)
    if info:
        db.touch_device(device_id)
    return info


def _require_auth(request: Request) -> Optional[dict]:
    """Like _get_auth but returns None so callers can redirect to /welcome."""
    return _get_auth(request)


# ── Onboarding routes ──

@app.get("/welcome", response_class=HTMLResponse)
async def welcome_page(request: Request):
    """Show the onboarding page — create a family or join one."""
    auth = _get_auth(request)
    if auth:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, "welcome.html", {"request": request})


@app.post("/create-family")
async def create_family(request: Request, response: Response,
                        family_name: str = Form(...),
                        your_name: str = Form("")):
    """Create a new family, member (admin), and device. Set cookie."""
    family_id = str(uuid4())
    member_id = str(uuid4())
    device_id = str(uuid4())
    display_name = your_name.strip() or "Family Member"

    db.create_family(family_id, family_name.strip() or "My Family")
    db.create_member(member_id, family_id, display_name, role="admin")
    db.create_device(device_id, member_id, request.headers.get("user-agent", ""))

    # Create initial invite code
    db.create_invite_code(family_id, member_id)

    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(
        COOKIE_NAME, device_id,
        max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax",
    )
    return resp


@app.get("/join/{code}", response_class=HTMLResponse)
async def join_page(request: Request, code: str):
    """Show the join page for an invite code."""
    auth = _get_auth(request)
    if auth:
        return RedirectResponse(url="/", status_code=303)

    invite = db.get_invite_code(code.upper())
    if not invite:
        return templates.TemplateResponse(request, "welcome.html", {
            "request": request, "join_error": "Invalid invite code.",
        })

    now = datetime.utcnow().isoformat()
    if invite["expires_at"] < now or invite["use_count"] >= invite["max_uses"]:
        return templates.TemplateResponse(request, "welcome.html", {
            "request": request, "join_error": "This invite code has expired.",
        })

    family = db.get_family(invite["family_id"])
    family_name = family["name"] if family else "a family"

    return templates.TemplateResponse(request, "join.html", {
        "request": request,
        "code": code.upper(),
        "family_name": family_name,
    })


@app.post("/join/{code}")
async def join_family(request: Request, code: str, your_name: str = Form("")):
    """Join an existing family via invite code."""
    code = code.upper()
    invite = db.get_invite_code(code)
    if not invite:
        return templates.TemplateResponse(request, "welcome.html", {
            "request": request, "join_error": "Invalid invite code.",
        })

    if not db.use_invite_code(code):
        return templates.TemplateResponse(request, "welcome.html", {
            "request": request, "join_error": "This invite code has expired or reached its limit.",
        })

    family_id = invite["family_id"]
    member_id = str(uuid4())
    device_id = str(uuid4())
    display_name = your_name.strip() or "Family Member"

    db.create_member(member_id, family_id, display_name, role="member")
    db.create_device(device_id, member_id, request.headers.get("user-agent", ""))

    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie(
        COOKIE_NAME, device_id,
        max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax",
    )
    return resp


@app.post("/join-by-code")
async def join_by_code(request: Request, code: str = Form(...)):
    """Redirect to the join page for a manually entered code."""
    code = code.strip().upper()
    if not code or len(code) != 6:
        return templates.TemplateResponse(request, "welcome.html", {
            "request": request, "join_error": "Please enter a valid 6-character code.",
        })
    return RedirectResponse(url=f"/join/{code}", status_code=303)


@app.post("/settings/regenerate-invite")
async def regenerate_invite(request: Request):
    """Generate a new invite code for the family."""
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)
    db.create_invite_code(auth["family_id"], auth["member_id"])
    return RedirectResponse(url="/", status_code=303)


# ── Helper functions ──

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
    if not time_str or not date_str:
        return None, None

    try:
        event_date = datetime.strptime(date_str, '%Y-%m-%d')
        time_str = time_str.strip().upper().replace(' ', '')

        patterns = [
            r'(\d{1,2}):?(\d{0,2})(AM|PM)?-(\d{1,2}):?(\d{0,2})(AM|PM)?',
            r'(\d{1,2})(AM|PM)-(\d{1,2})(AM|PM)'
        ]

        for pattern in patterns:
            match = re.match(pattern, time_str)
            if match:
                groups = match.groups()
                start_hour = int(groups[0])
                start_min = int(groups[1]) if groups[1] else 0
                start_period = groups[2] if groups[2] else 'AM'

                if start_period == 'PM' and start_hour != 12:
                    start_hour += 12
                elif start_period == 'AM' and start_hour == 12:
                    start_hour = 0

                start_time = event_date.replace(hour=start_hour, minute=start_min, second=0)

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

        start_time = event_date.replace(hour=9, minute=0, second=0)
        end_time = event_date.replace(hour=17, minute=0, second=0)
        return start_time, end_time

    except Exception:
        return None, None


def generate_google_calendar_url(event: Dict[str, Optional[str]]) -> str:
    base_url = "https://calendar.google.com/calendar/render"
    title = event.get('title') or 'Event'
    start_date = event.get('start_date') or event.get('date')
    end_date = event.get('end_date')
    time_str = event.get('time')
    location = event.get('location') or ''

    if start_date:
        start_dt, end_dt = parse_time_simple(time_str, start_date)
        if not start_dt:
            try:
                date_obj = datetime.strptime(start_date, '%Y-%m-%d')
                start_dt = date_obj.replace(hour=9, minute=0, second=0)
                end_dt = date_obj.replace(hour=17, minute=0, second=0)
            except:
                return ""
        start_formatted = start_dt.strftime('%Y%m%dT%H%M%S')
        end_formatted = end_dt.strftime('%Y%m%dT%H%M%S')
        dates = f"{start_formatted}/{end_formatted}"
    else:
        return ""

    description_parts = ["Event extracted by FamPilot"]
    if time_str:
        description_parts.append(f"Time: {time_str}")
    if end_date:
        description_parts.append(f"Multi-day event: {start_date} to {end_date}")
    description = "\\n".join(description_parts)

    params = [
        "action=TEMPLATE",
        f"text={quote(title)}",
        f"dates={dates}",
        f"details={quote(description)}"
    ]
    if location:
        params.append(f"location={quote(location)}")
    return f"{base_url}?{'&'.join(params)}"


def _build_risk_items(today_items: list, later_items: list) -> list:
    from datetime import date, timedelta
    now      = datetime.now()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    risk     = []

    for row in today_items:
        rtype = row["type"]
        title = row["title"] or "Untitled"

        if rtype in ("task", "reminder"):
            icon = "✅" if rtype == "task" else "🔔"
            risk.append({"icon": icon, "text": title, "when": "due today",
                         "id": row["id"], "type": rtype})

        elif rtype == "event":
            time_str = row["time"] or ""
            mt = re.search(r'(\d{1,2}):?(\d{2})?\s*(AM|PM)?', time_str, re.IGNORECASE) if time_str else None
            if mt:
                h = int(mt.group(1))
                m = int(mt.group(2)) if mt.group(2) else 0
                p = (mt.group(3) or "").upper()
                if p == "PM" and h != 12: h += 12
                if p == "AM" and h == 12: h = 0
                event_dt   = now.replace(hour=h, minute=m, second=0, microsecond=0)
                diff_mins  = (event_dt - now).total_seconds() / 60
                if -30 <= diff_mins <= 180:
                    if diff_mins <= 0:
                        when_label = "starting now"
                    elif diff_mins < 60:
                        mins = int(diff_mins)
                        when_label = f"in {mins} min{'s' if mins != 1 else ''}"
                    else:
                        hrs = round(diff_mins / 60, 1)
                        hrs_int = int(hrs)
                        when_label = f"in {hrs_int} hour{'s' if hrs_int != 1 else ''}"
                    risk.append({"icon": "📅", "text": title, "when": when_label,
                                 "id": row["id"], "type": "event"})

    for row in later_items:
        if row["start_date"] == tomorrow and row["type"] == "task" and row["priority"] == "high":
            risk.append({"icon": "✅", "text": row["title"] or "Untitled",
                         "when": "due tomorrow", "id": row["id"], "type": "task"})

    return risk


def _build_daily_briefing(today_items: list) -> list:
    lines = []
    for row in today_items:
        rtype = row["type"]
        title = row["title"] or "Untitled"
        if rtype == "event":
            detail = f" · {row['time']}" if row["time"] else ""
            detail += f" @ {row['location']}" if row["location"] else ""
            lines.append({"icon": "📅", "text": title + detail, "type": "event", "id": row["id"]})
        elif rtype == "task":
            pri = row["priority"] or "medium"
            pri_label = f" · {pri} priority" if pri != "medium" else ""
            lines.append({"icon": "✅", "text": title + pri_label, "type": "task", "id": row["id"]})
        else:
            when = f" · {row['remind_at']}" if row["remind_at"] else ""
            lines.append({"icon": "🔔", "text": title + when, "type": "reminder", "id": row["id"]})
    return lines


def _local_today():
    from datetime import date
    tz_name = os.getenv("APP_TIMEZONE")
    if tz_name:
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz_name)).date()
        except Exception:
            pass
    return date.today()


def _base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host  = request.headers.get("x-forwarded-host", request.url.netloc)
    return f"{proto}://{host}"


# ── Main routes ──

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render the home page with upload form and upcoming items."""
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)

    family_id = auth["family_id"]
    today_str = _local_today().isoformat()
    all_upcoming = db.get_upcoming_items(family_id)
    today_items = [r for r in all_upcoming if r["start_date"] == today_str]
    later_items = [r for r in all_upcoming if r["start_date"] != today_str]
    risk_items = _build_risk_items(today_items, later_items)
    risk_ids   = {item["id"] for item in risk_items}
    briefing   = [b for b in _build_daily_briefing(today_items) if b["id"] not in risk_ids]

    # Get or create invite code for sharing
    invite_code = db.get_active_invite_code(family_id)
    if not invite_code:
        invite_code = db.create_invite_code(family_id, auth["member_id"])
    join_url = f"{_base_url(request)}/join/{invite_code}"

    # Legacy family share page
    family_token = db.get_or_create_family_token()
    family_url   = f"{_base_url(request)}/family/{family_token}"

    members = db.get_family_members(family_id)

    return templates.TemplateResponse(request, "index.html", {
        "request":             request,
        "nav_page":            "home",
        "today_str":           today_str,
        "risk_items":          risk_items,
        "briefing":            briefing,
        "later_items":         later_items[:3],
        "later_total":         len(later_items),
        "triggered_reminders": db.get_recent_reminders(family_id),
        "family_url":          family_url,
        "auth":                auth,
        "invite_code":         invite_code,
        "join_url":            join_url,
        "members":             members,
    })


@app.post("/dismiss-reminder/{item_id}")
async def dismiss_reminder(item_id: str):
    db.dismiss_reminder(item_id)
    return RedirectResponse(url="/", status_code=303)


@app.get("/share-target")
async def share_target_get(request: Request):
    return RedirectResponse(url="/", status_code=303)


@app.post("/share-target")
async def share_target_post(request: Request,
                             file: UploadFile = File(None),
                             text: str = Form(None),
                             title: str = Form(None)):
    if file and file.filename:
        return await upload_image(request, file=file)
    if text or title:
        combined = "\n".join(filter(None, [title, text]))
        return await process_text(request, text=combined)
    return RedirectResponse(url="/", status_code=303)


@app.post("/upload")
async def upload_image(request: Request, file: UploadFile = File(...)):
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)

    family_id = auth["family_id"]
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
            batch = classify_and_extract_multi_from_image(str(file_path), api_key)
        except Exception as e:
            return templates.TemplateResponse(request, "result.html", {
                "request": request,
                "error": f"Failed to analyze image: {str(e)}"
            })
        finally:
            if file_path and file_path.exists():
                file_path.unlink()

        items         = batch["items"]
        group_title   = batch.get("group_title") or ""
        group_summary = batch.get("group_summary")

        if len(items) == 1:
            item_id = str(uuid4())
            flat = items[0]
            db.save_flat_item(item_id, flat, image_path=file.filename,
                              group_title=group_title, group_summary=group_summary,
                              family_id=family_id)
            result = _flat_to_result(flat)
            return _render_result(request, result, item_id, image_path=file.filename, skip_db=True)

        batch_id = str(uuid4())
        batch_store[batch_id] = {
            "items": items, "source": file.filename, "is_image": True,
            "group_id": str(uuid4()), "group_title": group_title, "group_summary": group_summary,
            "family_id": family_id,
        }
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
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)

    family_id = auth["family_id"]
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
        batch = classify_and_extract_multi(text.strip(), api_key)
    except Exception as e:
        return templates.TemplateResponse(request, "result.html", {
            "request": request,
            "error": f"Failed to analyze text: {str(e)}"
        })

    items         = batch["items"]
    group_title   = batch.get("group_title") or ""
    group_summary = batch.get("group_summary")

    if len(items) == 1:
        item_id = str(uuid4())
        flat = items[0]
        db.save_flat_item(item_id, flat, source_text=text,
                          group_title=group_title, group_summary=group_summary,
                          family_id=family_id)
        result = _flat_to_result(flat)
        return _render_result(request, result, item_id, source_text=text, skip_db=True)

    batch_id = str(uuid4())
    batch_store[batch_id] = {
        "items": items, "source": text, "is_image": False,
        "group_id": str(uuid4()), "group_title": group_title, "group_summary": group_summary,
        "family_id": family_id,
    }
    return RedirectResponse(url=f"/review/{batch_id}", status_code=303)


def _result_to_calendar_data(result: Dict) -> Optional[Dict]:
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
        from datetime import date
        remind_date = date.today().strftime("%Y-%m-%d")
        remind_time = "8:00 AM"
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
    items_store[item_id] = result
    if not skip_db:
        auth = _get_auth(request)
        family_id = auth["family_id"] if auth else None
        db.save_item(item_id, result, source_text=source_text,
                     image_path=image_path, family_id=family_id)

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
    batch = batch_store.get(batch_id)
    if not batch:
        return templates.TemplateResponse(request, "result.html", {
            "request": request,
            "error": "Review session expired. Please upload again.",
        })
    return templates.TemplateResponse(request, "review.html", {
        "request":      request,
        "batch_id":     batch_id,
        "items":        list(enumerate(batch["items"])),
        "total":        len(batch["items"]),
        "group_title":  batch.get("group_title") or "",
        "group_summary": batch.get("group_summary"),
        "nav_page":     None,
    })


@app.post("/review/{batch_id}/save")
async def save_batch(request: Request, batch_id: str):
    batch = batch_store.get(batch_id)
    source_text   = batch.get("source") if batch and not batch.get("is_image") else None
    image_path    = batch.get("source") if batch and batch.get("is_image")     else None
    group_id      = batch.get("group_id")      if batch else None
    group_title   = batch.get("group_title")   if batch else None
    group_summary = batch.get("group_summary") if batch else None
    family_id     = batch.get("family_id")     if batch else None

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
        db.save_flat_item(str(uuid4()), flat, source_text=source_text, image_path=image_path,
                          group_id=group_id, group_title=group_title, group_summary=group_summary,
                          family_id=family_id)

    batch_store.pop(batch_id, None)
    return RedirectResponse(url="/history", status_code=303)


@app.get("/family/{token}", response_class=HTMLResponse)
async def family_view(request: Request, token: str):
    """Shared read-only family week view — no login required."""
    stored = db.get_setting("family_token")
    if not stored or token != stored:
        return templates.TemplateResponse(request, "family.html", {
            "request": request, "invalid": True, "days": [], "has_items": False,
        })

    from collections import defaultdict

    # For the legacy share page, find the first family or show all items
    auth = _get_auth(request)
    family_id = auth["family_id"] if auth else None

    today = _local_today()

    if family_id:
        rows = db.get_family_week(family_id)
    else:
        rows = []

    by_date: dict = defaultdict(list)
    for row in rows:
        by_date[row["start_date"]].append(row)

    days = []
    for i in range(8):
        d     = today + timedelta(days=i)
        d_str = d.isoformat()
        if d_str not in by_date:
            continue
        if i == 0:
            label = "Today"
        elif i == 1:
            label = "Tomorrow"
        else:
            label = f"{d.strftime('%A, %b')} {d.day}"

        day_items = []
        for row in by_date[d_str]:
            cal_url = ""
            if row["type"] == "event":
                cal_data = {
                    "title":      row["title"],
                    "start_date": row["start_date"],
                    "end_date":   row["end_date"],
                    "time":       row["time"],
                    "location":   row["location"],
                }
                cal_url = generate_google_calendar_url(cal_data)
            day_items.append({"row": row, "cal_url": cal_url})

        days.append({"label": label, "date": d_str, "entries": day_items})

    return templates.TemplateResponse(request, "family.html", {
        "request":   request,
        "invalid":   False,
        "days":      days,
        "has_items": bool(rows),
    })


@app.post("/settings/regenerate-link")
async def regenerate_family_link(request: Request):
    import secrets
    db.set_setting("family_token", secrets.token_urlsafe(12))
    return RedirectResponse(url="/", status_code=303)


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)

    rows = db.get_history(auth["family_id"], limit=100)
    return templates.TemplateResponse(request, "history.html", {
        "request": request,
        "items": rows,
        "nav_page": "history",
    })


@app.get("/history/{item_id}", response_class=HTMLResponse)
async def history_detail(request: Request, item_id: str):
    row = db.get_item(item_id)
    if not row:
        return templates.TemplateResponse(request, "result.html", {
            "request": request,
            "error": "Item not found in history.",
        })

    result = db.row_to_result(row)
    items_store[item_id] = result

    context = {
        "request":        request,
        "result":         result,
        "item_id":        item_id,
        "low_confidence": False,
        "from_history":   True,
        "completed":      bool(row["completed"]),
    }
    cal_data = _result_to_calendar_data(result)
    if cal_data:
        context["calendar_url"] = generate_google_calendar_url(cal_data)
    return templates.TemplateResponse(request, "result.html", context)


@app.get("/history/{item_id}/edit", response_class=HTMLResponse)
async def edit_item_form(request: Request, item_id: str):
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
    row = db.get_item(item_id)
    if not row:
        return RedirectResponse(url="/history", status_code=303)

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
    if item_id in items_store:
        items_store.pop(item_id)
    return RedirectResponse(url=f"/history/{item_id}", status_code=303)


@app.post("/history/{item_id}/complete")
async def complete_item(item_id: str):
    db.complete_item(item_id)
    return JSONResponse({"ok": True})


@app.post("/history/{item_id}/uncomplete")
async def uncomplete_item(item_id: str):
    db.uncomplete_item(item_id)
    return JSONResponse({"ok": True})


@app.post("/history/{item_id}/delete")
async def delete_item(request: Request, item_id: str):
    db.delete_item(item_id)
    items_store.pop(item_id, None)
    return RedirectResponse(url="/history", status_code=303)


if __name__ == "__main__":
    import uvicorn

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
