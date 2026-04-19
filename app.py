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
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Tuple
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, UploadFile, File, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import db
import push as push_module

# Import functions from main.py
from main import (
    extract_text_from_image,
    classify_and_extract,
    classify_and_extract_from_image,
    classify_and_extract_multi,
    classify_and_extract_multi_from_image,
    suggest_meals_from_pantry,
    suggest_meals_from_photo,
    extract_receipt_items,
    estimate_expiry_date,
    classify_item_category,
    answer_family_question,
    authenticate_google_calendar,
    create_calendar_event,
    GOOGLE_CALENDAR_AVAILABLE,
    TESSERACT_AVAILABLE
)

COOKIE_NAME = "fp_device"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365 * 2  # 2 years


async def _reminder_checker():
    """Background task: poll every 60 s, send due reminders + daily briefing."""
    last_briefing_date = None
    last_recap_weekday = None
    while True:
        await asyncio.sleep(60)
        try:
            # Send due reminders
            for row in db.get_due_reminders():
                print(
                    f"[🔔 REMINDER] {(row['type'] or 'item').upper()}: "
                    f"{row['title']} — was due at {row['reminder_time']}",
                    flush=True,
                )
                db.mark_reminder_sent(row["id"])

            # Morning briefing at 8am (check every minute)
            now = datetime.now()
            tz_name = os.getenv("APP_TIMEZONE")
            if tz_name:
                try:
                    from zoneinfo import ZoneInfo
                    now = datetime.now(ZoneInfo(tz_name))
                except Exception:
                    pass
            today = now.date()

            # Send morning briefing at 8:00
            if now.hour == 8 and now.minute < 2 and last_briefing_date != today:
                last_briefing_date = today
                print("[📋 MORNING BRIEFING] Sending...", flush=True)
                for fid in db.get_all_family_ids():
                    briefing = db.build_morning_briefing(fid)
                    sent = push_module.send_to_family(
                        fid, title=briefing["title"], body=briefing["body"],
                        url="/", tag="morning-briefing")
                    if sent:
                        print(f"  → Sent to family {fid[:8]}... ({sent} devices)", flush=True)

            # Weekly recap Sunday at 7pm
            if now.weekday() == 6 and now.hour == 19 and now.minute < 2 and last_recap_weekday != today:
                last_recap_weekday = today
                print("[📊 WEEKLY RECAP] Sending...", flush=True)
                for fid in db.get_all_family_ids():
                    recap = db.build_weekly_recap(fid)
                    sent = push_module.send_to_family(
                        fid, title=recap["title"], body=recap["body"],
                        url="/", tag="weekly-recap")
                    if sent:
                        print(f"  → Sent to family {fid[:8]}... ({sent} devices)", flush=True)

        except Exception as exc:
            print(f"[BACKGROUND ERROR] {exc}", flush=True)


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


@app.get("/invite-qr/{code}")
async def invite_qr(request: Request, code: str):
    """Generate a QR code PNG for an invite link."""
    import segno
    import io
    from fastapi.responses import StreamingResponse

    url = f"{_base_url(request)}/join/{code.upper()}"

    qr = segno.make(url)
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=8, border=2, dark="#667eea")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


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


# ── SEO routes ──

@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return "User-agent: *\nAllow: /\nDisallow: /history\nDisallow: /settings\nSitemap: https://fampilot-37ac.onrender.com/sitemap.xml"

@app.get("/sitemap.xml", response_class=PlainTextResponse)
async def sitemap():
    return """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://fampilot-37ac.onrender.com/</loc><priority>1.0</priority></url>
  <url><loc>https://fampilot-37ac.onrender.com/welcome</loc><priority>0.8</priority></url>
</urlset>"""

# ── Main routes ──

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Render the home page with upload form and upcoming items."""
    auth = _require_auth(request)
    if not auth:
        return templates.TemplateResponse(request, "landing.html", {"request": request})

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

    # Build smart briefing summary
    from datetime import datetime as dt
    hour = dt.now().hour
    if hour < 12:
        greeting = "Good morning"
    elif hour < 17:
        greeting = "Good afternoon"
    else:
        greeting = "Good evening"

    summary_parts = []
    if today_items:
        if len(today_items) == 1:
            t = today_items[0]
            time_str = f" at {t['time']}" if t.get('time') else ""
            summary_parts.append(f"You have 1 event today — {t['title']}{time_str}.")
        else:
            summary_parts.append(f"You have {len(today_items)} events today.")
    if later_items:
        summary_parts.append(f"{len(later_items)} more coming up this week.")

    lists = db.get_lists(family_id)
    total_list_items = 0
    for lst in lists:
        items = db.get_list_items(lst["id"])
        total_list_items += sum(1 for i in items if not i.get("checked"))
    if total_list_items > 0:
        summary_parts.append(f"{total_list_items} items on your lists.")

    chores_today = db.get_chores_with_status(family_id, today_str)
    pending_chores = [c for c in chores_today if not c.get("done_today")]
    if pending_chores:
        if len(pending_chores) == 1:
            summary_parts.append(f"\"{pending_chores[0]['title']}\" still pending.")
        else:
            summary_parts.append(f"{len(pending_chores)} chores still pending.")

    # Stale pantry items
    stale_pantry = db.get_stale_pantry_items(family_id)
    if stale_pantry:
        stale_names = ", ".join(s["text"] for s in stale_pantry[:3])
        summary_parts.append(f"Might be running low on: {stale_names}.")

    if not summary_parts:
        summary_parts.append("All clear — nothing scheduled.")

    smart_summary = f"{greeting}, {auth['display_name']}. " + " ".join(summary_parts)

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
        "usage":               db.get_usage_info(family_id),
        "recent_activity":     db.get_recent_activity(family_id, limit=10),
        "smart_summary":       smart_summary,
        "suggestions":         db.get_pattern_suggestions(family_id),
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

    if not db.can_scan(family_id):
        return templates.TemplateResponse(request, "result.html", {
            "request": request,
            "error": "You've used all 5 free AI scans this month. Upgrade to FamPilot Pro for unlimited scans.",
            "show_upgrade": True,
        })

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

    if not db.can_scan(family_id):
        return templates.TemplateResponse(request, "result.html", {
            "request": request,
            "error": "You've used all 5 free AI scans this month. Upgrade to FamPilot Pro for unlimited scans.",
            "show_upgrade": True,
        })

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


# ── Calendar View ──

MEMBER_COLORS = ["#667eea", "#e91e63", "#43a047", "#ff9800", "#9c27b0", "#00bcd4", "#795548", "#607d8b"]

@app.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request, week: int = 0):
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)
    family_id = auth["family_id"]
    cal = db.get_calendar_week(family_id, week_offset=week)
    members = db.get_family_members(family_id)
    member_colors = {m["display_name"]: MEMBER_COLORS[i % len(MEMBER_COLORS)]
                     for i, m in enumerate(members)}
    return templates.TemplateResponse(request, "calendar.html", {
        "request": request,
        "cal": cal,
        "week": week,
        "member_colors": member_colors,
        "nav_page": "calendar",
        "auth": auth,
    })


# ── Email Forwarding → Events ──

@app.post("/api/email-to-events")
async def email_to_events(request: Request, email_text: str = Form(...)):
    """Parse an email body and extract all events/dates."""
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "API key not configured"}, status_code=500)

    family_id = auth["family_id"]
    try:
        result = classify_and_extract_multi(email_text, api_key)
        items_saved = []
        for flat in result.get("items", []):
            item_id = str(uuid4())
            db.save_flat_item(
                item_id, flat,
                source_text=email_text,
                group_id=result.get("group_id"),
                group_title=result.get("group_title"),
                group_summary=result.get("group_summary"),
                family_id=family_id,
            )
            items_saved.append({"id": item_id, "title": flat.get("title"), "date": flat.get("start_date"), "type": flat.get("type")})
        db.log_activity(family_id, auth["display_name"],
                        f"added {len(items_saved)} events from email", "email_import")
        return JSONResponse({
            "ok": True,
            "count": len(items_saved),
            "items": items_saved,
            "group_title": result.get("group_title"),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Global Search ──

@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = ""):
    """Legacy: redirect into Ask FamPilot so search and ask share one entry point."""
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)
    query = (q or "").strip()
    target = f"/ask?q={quote(query)}" if query else "/ask"
    return RedirectResponse(url=target, status_code=303)


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


# ── Shopping Lists ──

@app.get("/lists", response_class=HTMLResponse)
async def lists_page(request: Request):
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)
    lists = db.get_list_summary(auth["family_id"])
    # Quick spend summary for the month for the top-of-page card
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    month_items = db.get_priced_items(auth["family_id"], since=month_start)
    month_total = sum((i["price"] or 0) * (i["quantity"] or 1) for i in month_items)
    return templates.TemplateResponse(request, "lists.html", {
        "request": request,
        "lists": lists,
        "month_spend_total": month_total,
        "month_spend_count": len(month_items),
        "nav_page": "lists",
        "auth": auth,
    })


@app.get("/spending", response_class=HTMLResponse)
async def spending_page(request: Request):
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)
    family_id = auth["family_id"]
    today = date.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    month_start = today.replace(day=1).isoformat()

    all_items = db.get_priced_items(family_id)
    wasted = db.get_wasted_pantry_items(family_id)

    def line_total(it) -> float:
        return float(it["price"] or 0) * int(it["quantity"] or 1)

    def bucket(items, since):
        filtered = [i for i in items if (i["created_at"] or "") >= since]
        by_cat: dict[str, float] = {}
        by_list: dict[str, float] = {}
        by_item: dict[str, float] = {}
        for i in filtered:
            amt = line_total(i)
            cat = classify_item_category(i["text"])
            by_cat[cat] = by_cat.get(cat, 0) + amt
            by_list[i["list_name"]] = by_list.get(i["list_name"], 0) + amt
            key = i["text"].strip().lower()
            by_item[key] = by_item.get(key, 0) + amt
        return {
            "total": sum(line_total(i) for i in filtered),
            "count": len(filtered),
            "by_category": sorted(by_cat.items(), key=lambda kv: -kv[1]),
            "by_list": sorted(by_list.items(), key=lambda kv: -kv[1]),
            "top_items": sorted(by_item.items(), key=lambda kv: -kv[1])[:8],
        }

    week = bucket(all_items, week_start)
    month = bucket(all_items, month_start)
    all_time = bucket(all_items, "")

    # Previous month for trend comparison
    first_of_this_month = today.replace(day=1)
    last_of_prev_month = first_of_this_month - timedelta(days=1)
    prev_month_start = last_of_prev_month.replace(day=1).isoformat()
    prev_month_items = [
        i for i in all_items
        if prev_month_start <= (i["created_at"] or "") < month_start
    ]
    prev_month_total = sum(line_total(i) for i in prev_month_items)
    if prev_month_total > 0:
        month_change_pct = round((month["total"] - prev_month_total) / prev_month_total * 100)
    else:
        month_change_pct = None

    wasted_total = 0.0
    wasted_with_price = []
    for w in wasted:
        if w["price"] and w["price"] > 0:
            amt = float(w["price"]) * int(w["quantity"] or 1)
            wasted_total += amt
            wasted_with_price.append({
                "text": w["text"],
                "amount": amt,
                "expires_at": w["expires_at"],
            })
    wasted_with_price.sort(key=lambda w: -w["amount"])

    return templates.TemplateResponse(request, "spending.html", {
        "request": request,
        "nav_page": "lists",
        "auth": auth,
        "week": week,
        "month": month,
        "all_time": all_time,
        "month_change_pct": month_change_pct,
        "prev_month_total": prev_month_total,
        "wasted_total": wasted_total,
        "wasted_count": len(wasted_with_price),
        "wasted_untracked": len([w for w in wasted if not (w["price"] and w["price"] > 0)]),
        "wasted_items": wasted_with_price[:10],
    })


@app.post("/lists/create")
async def create_list(request: Request, name: str = Form(...), icon: str = Form("🛒")):
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)
    list_id = str(uuid4())
    list_name = name.strip() or "Shopping List"
    db.create_list(list_id, auth["family_id"], list_name, icon)
    db.log_activity(auth["family_id"], auth["display_name"], f"created list \"{list_name}\"", "list_created")
    return RedirectResponse(url=f"/lists/{list_id}", status_code=303)


@app.get("/lists/{list_id}", response_class=HTMLResponse)
async def view_list(request: Request, list_id: str):
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)
    lst = db.get_list(list_id)
    if not lst or lst["family_id"] != auth["family_id"]:
        return RedirectResponse(url="/lists", status_code=303)
    items = db.get_list_items(list_id)
    is_pantry = lst["name"].lower() in ("pantry", "inventory", "home inventory")
    if is_pantry:
        today = date.today()
        augmented = []
        for i in items:
            d = dict(i)
            exp = d.get("expires_at")
            d["days_left"] = None
            if exp:
                try:
                    d["days_left"] = (date.fromisoformat(exp) - today).days
                except Exception:
                    pass
            augmented.append(d)
        items = augmented
    unchecked = [i for i in items if not i["checked"]]
    checked = [i for i in items if i["checked"]]
    spending = db.get_list_spending(list_id)
    members = db.get_family_members(auth["family_id"])
    all_suggestions = db.get_pattern_suggestions(auth["family_id"])
    suggestions = [s for s in all_suggestions if s.get("list_id") == list_id]
    return templates.TemplateResponse(request, "list_detail.html", {
        "request": request,
        "list": lst,
        "unchecked": unchecked,
        "checked": checked,
        "spending": spending,
        "members": members,
        "is_pantry": is_pantry,
        "suggestions": suggestions,
        "nav_page": "lists",
        "auth": auth,
    })


@app.post("/lists/{list_id}/add")
async def add_to_list(request: Request, list_id: str, text: str = Form(...)):
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    lst = db.get_list(list_id)
    if not lst or lst["family_id"] != auth["family_id"]:
        return JSONResponse({"error": "not found"}, status_code=404)
    # Build set of existing items for duplicate detection
    existing_items = {i["text"].lower().strip() for i in db.get_list_items(list_id) if not i["checked"]}
    is_pantry_list = lst["name"].lower() in ("pantry", "inventory", "home inventory")
    # Support adding multiple items separated by newlines
    # Parse optional quantity: "tomatoes x3", "tomatoes (3)", "3 tomatoes"
    qty_pattern = re.compile(r'^(.+?)\s*[x×]\s*(\d+)$|^(.+?)\s*\((\d+)\)$|^(\d+)\s+(.+)$', re.IGNORECASE)
    added = []
    duplicates = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        qty = 1
        item_text = line
        m = qty_pattern.match(line)
        if m:
            if m.group(1) and m.group(2):  # "tomatoes x3"
                item_text, qty = m.group(1).strip(), int(m.group(2))
            elif m.group(3) and m.group(4):  # "tomatoes (3)"
                item_text, qty = m.group(3).strip(), int(m.group(4))
            elif m.group(5) and m.group(6):  # "3 tomatoes"
                item_text, qty = m.group(6).strip(), int(m.group(5))
        if item_text.lower().strip() in existing_items:
            duplicates.append(item_text)
            continue
        item_id = str(uuid4())
        expires_at = estimate_expiry_date(item_text) if is_pantry_list else None
        db.add_list_item(item_id, list_id, item_text, added_by=auth["display_name"], quantity=qty, expires_at=expires_at)
        added.append({"id": item_id, "text": item_text, "quantity": qty})
        existing_items.add(item_text.lower().strip())
    if added:
        items_text = ", ".join(a["text"] for a in added[:3])
        if len(added) > 3:
            items_text += f" +{len(added)-3} more"
        db.log_activity(auth["family_id"], auth["display_name"], f"added {items_text} to {lst['name']}", "list_item_added")
        push_module.send_to_family(
            auth["family_id"],
            title=f"{auth['display_name']} added to {lst['name']}",
            body=items_text,
            url=f"/lists/{list_id}",
            tag=f"list-{list_id}",
            exclude_member_id=auth["member_id"],
        )
    # If request is AJAX, return JSON; otherwise redirect
    if request.headers.get("accept", "").startswith("application/json"):
        return JSONResponse({"ok": True, "added": added, "duplicates": duplicates})
    return RedirectResponse(url=f"/lists/{list_id}", status_code=303)


@app.post("/lists/{list_id}/check/{item_id}")
async def check_item(list_id: str, item_id: str):
    db.check_list_item(item_id)
    return JSONResponse({"ok": True})


@app.post("/lists/{list_id}/uncheck/{item_id}")
async def uncheck_item(list_id: str, item_id: str):
    db.uncheck_list_item(item_id)
    return JSONResponse({"ok": True})


@app.post("/lists/{list_id}/delete-item/{item_id}")
async def delete_list_item_route(list_id: str, item_id: str):
    db.delete_list_item(item_id)
    return JSONResponse({"ok": True})


@app.post("/lists/{list_id}/qty/{item_id}")
async def update_quantity(list_id: str, item_id: str, qty: int = Form(...)):
    db.update_list_item_quantity(item_id, qty)
    return JSONResponse({"ok": True, "quantity": max(1, qty)})


@app.post("/lists/{list_id}/note/{item_id}")
async def update_note(list_id: str, item_id: str, note: str = Form("")):
    db.update_list_item_note(item_id, note)
    return JSONResponse({"ok": True})


@app.post("/lists/{list_id}/assign/{item_id}")
async def assign_item(list_id: str, item_id: str, assigned_to: str = Form("")):
    db.update_list_item_assigned(item_id, assigned_to)
    return JSONResponse({"ok": True})


@app.post("/lists/{list_id}/price/{item_id}")
async def set_price(list_id: str, item_id: str, price: float = Form(0)):
    db.update_list_item_price(item_id, price)
    return JSONResponse({"ok": True})


@app.post("/lists/{list_id}/running-low/{item_id}")
async def running_low(request: Request, list_id: str, item_id: str):
    """Move a pantry item to the grocery list."""
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    family_id = auth["family_id"]
    # Get the pantry item
    item = None
    for li in db.get_list_items(list_id):
        if li["id"] == item_id:
            item = li
            break
    if not item:
        return JSONResponse({"error": "not found"}, status_code=404)
    # Find or create a Groceries list
    grocery_list = None
    for l in db.get_lists(family_id):
        if l["name"].lower() in ("groceries", "grocery list", "grocery"):
            grocery_list = l
            break
    if not grocery_list:
        grocery_id = str(uuid4())
        db.create_list(grocery_id, family_id, "Groceries", icon="🛒")
        grocery_list = {"id": grocery_id, "name": "Groceries"}
    # Add to grocery list
    new_id = str(uuid4())
    db.add_list_item(new_id, grocery_list["id"], item["text"], added_by=auth["display_name"])
    db.log_activity(family_id, auth["display_name"], f"marked {item['text']} as running low", "running_low")
    return JSONResponse({
        "ok": True,
        "grocery_list_id": grocery_list["id"],
        "grocery_list_name": grocery_list["name"],
        "item_text": item["text"],
    })


@app.post("/lists/{list_id}/share")
async def share_list(request: Request, list_id: str):
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    lst = db.get_list(list_id)
    if not lst or lst["family_id"] != auth["family_id"]:
        return JSONResponse({"error": "not found"}, status_code=404)
    token = db.get_or_create_share_token(list_id)
    share_url = f"{_base_url(request)}/shared/{token}"
    return JSONResponse({"ok": True, "url": share_url, "token": token})


@app.get("/shared/{token}", response_class=HTMLResponse)
async def view_shared_list(request: Request, token: str):
    lst = db.get_list_by_share_token(token)
    if not lst:
        return HTMLResponse("<h2>List not found</h2>", status_code=404)
    items = db.get_list_items(lst["id"])
    unchecked = [i for i in items if not i["checked"]]
    checked = [i for i in items if i["checked"]]
    return templates.TemplateResponse(request, "shared_list.html", {
        "request": request, "list": lst,
        "unchecked": unchecked, "checked": checked,
    })


@app.post("/lists/{list_id}/clear-checked")
async def clear_checked(request: Request, list_id: str):
    db.clear_checked_items(list_id)
    return RedirectResponse(url=f"/lists/{list_id}", status_code=303)


@app.post("/lists/{list_id}/delete")
async def delete_list_route(request: Request, list_id: str):
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)
    lst = db.get_list(list_id)
    if lst and lst["family_id"] == auth["family_id"]:
        db.delete_list(list_id)
    return RedirectResponse(url="/lists", status_code=303)


# ── Chores ──

@app.get("/chores", response_class=HTMLResponse)
async def chores_page(request: Request):
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)
    today_str = _local_today().isoformat()
    chores = db.get_chores_with_status(auth["family_id"], today_str)
    members = db.get_family_members(auth["family_id"])
    return templates.TemplateResponse(request, "chores.html", {
        "request": request,
        "chores": chores,
        "members": members,
        "today_str": today_str,
        "nav_page": "chores",
        "auth": auth,
    })


@app.post("/chores/create")
async def create_chore_route(request: Request,
                              title: str = Form(...),
                              assigned_to: str = Form(""),
                              recurrence: str = Form("daily"),
                              icon: str = Form("🧹")):
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)
    chore_id = str(uuid4())
    chore_title = title.strip()
    db.create_chore(chore_id, auth["family_id"], chore_title,
                    icon=icon, assigned_to=assigned_to, recurrence=recurrence)
    db.log_activity(auth["family_id"], auth["display_name"], f"created chore \"{chore_title}\"", "chore_created")
    return RedirectResponse(url="/chores", status_code=303)


@app.post("/chores/{chore_id}/done")
async def mark_chore_done(request: Request, chore_id: str):
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    today_str = _local_today().isoformat()
    log_id = str(uuid4())
    db.log_chore_done(log_id, chore_id, auth["display_name"], today_str)
    chore = db.get_chore(chore_id)
    if chore:
        db.log_activity(auth["family_id"], auth["display_name"], f"completed \"{chore['title']}\"", "chore_done")
        push_module.send_to_family(
            auth["family_id"],
            title=f"{auth['display_name']} completed a chore",
            body=chore['title'],
            url="/chores",
            tag=f"chore-{chore_id}",
            exclude_member_id=auth["member_id"],
        )
    streak = db.get_chore_streak(chore_id, today_str)
    return JSONResponse({"ok": True, "streak": streak})


@app.post("/chores/{chore_id}/undo")
async def undo_chore(request: Request, chore_id: str):
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    today_str = _local_today().isoformat()
    db.undo_chore_done(chore_id, today_str)
    return JSONResponse({"ok": True})


@app.post("/chores/{chore_id}/delete")
async def delete_chore_route(request: Request, chore_id: str):
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)
    chore = db.get_chore(chore_id)
    if chore and chore["family_id"] == auth["family_id"]:
        db.delete_chore(chore_id)
    return RedirectResponse(url="/chores", status_code=303)


# ── Meal Planning ──

@app.get("/meals", response_class=HTMLResponse)
async def meals_page(request: Request):
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)
    plan = db.get_latest_meal_plan(auth["family_id"])
    meals = json.loads(plan["meals_json"]) if plan else None
    lists = db.get_lists(auth["family_id"])
    return templates.TemplateResponse(request, "meals.html", {
        "request": request,
        "plan": plan,
        "meals": meals,
        "lists": lists,
        "nav_page": "meals",
        "auth": auth,
    })


@app.post("/meals/generate")
async def generate_meal_plan(request: Request,
                              days: int = Form(7),
                              preferences: str = Form("")):
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return templates.TemplateResponse(request, "meals.html", {
            "request": request, "plan": None, "meals": None,
            "lists": [], "nav_page": "meals", "auth": auth,
            "error": "ANTHROPIC_API_KEY not set.",
        })

    family_id = auth["family_id"]
    members = db.get_family_members(family_id)
    member_count = len(members)

    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2048,
        messages=[{"role": "user", "content": f"""Generate a {days}-day family meal plan.

Family size: {member_count} people
Preferences/restrictions: {preferences or 'None specified'}

Return ONLY a JSON object with this structure:
{{
  "days": [
    {{
      "day": "Monday",
      "breakfast": {{"name": "...", "time": "15 min"}},
      "lunch": {{"name": "...", "time": "20 min"}},
      "dinner": {{"name": "...", "time": "30 min"}},
      "snack": {{"name": "..."}}
    }}
  ],
  "grocery_list": ["item 1", "item 2", ...]
}}

Rules:
- Practical, family-friendly meals (not fancy restaurant dishes)
- Vary cuisines across the week
- Include prep time estimates
- The grocery list should include ALL ingredients needed, organized logically
- Assume a basic pantry (salt, pepper, oil, common spices already available)
- Keep it realistic for busy parents
"""}],
    )

    try:
        from main import clean_json_response
        cleaned = clean_json_response(msg.content[0].text)
        meals = json.loads(cleaned)
    except Exception:
        return templates.TemplateResponse(request, "meals.html", {
            "request": request, "plan": None, "meals": None,
            "lists": [], "nav_page": "meals", "auth": auth,
            "error": "Failed to generate meal plan. Please try again.",
        })

    plan_id = str(uuid4())
    db.save_meal_plan(plan_id, family_id, json.dumps(meals),
                      days=days, preferences=preferences)

    lists = db.get_lists(family_id)
    return templates.TemplateResponse(request, "meals.html", {
        "request": request,
        "plan": {"id": plan_id},
        "meals": meals,
        "lists": lists,
        "nav_page": "meals",
        "auth": auth,
        "message": f"Generated a {days}-day meal plan!",
    })


@app.post("/meals/add-to-list")
async def meals_add_to_list(request: Request,
                             list_id: str = Form(""),
                             items_json: str = Form("[]")):
    """Add grocery items from a meal plan to a shopping list."""
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    family_id = auth["family_id"]
    items = json.loads(items_json)

    # Smart deduction: skip items already in pantry
    pantry_items = {r["text"].lower().strip() for r in db.get_pantry_items(family_id)}
    skipped = []

    # Create a new list if none selected
    if not list_id:
        list_id = str(uuid4())
        db.create_list(list_id, family_id, "Meal Plan Groceries", icon="🥗")

    lst = db.get_list(list_id)
    if not lst or lst["family_id"] != family_id:
        return JSONResponse({"error": "list not found"}, status_code=404)

    added = []
    for item_text in items:
        if not item_text.strip():
            continue
        if item_text.strip().lower() in pantry_items:
            skipped.append(item_text.strip())
            continue
        item_id = str(uuid4())
        db.add_list_item(item_id, list_id, item_text.strip(), added_by="Meal Planner")
        added.append(item_text.strip())

    return JSONResponse({
        "ok": True,
        "list_id": list_id,
        "list_name": lst["name"] if lst else "Meal Plan Groceries",
        "count": len(added),
        "skipped": skipped,
        "skipped_count": len(skipped),
    })


# ── "What's for dinner?" — AI meal suggestions ──

@app.get("/whats-for-dinner", response_class=HTMLResponse)
async def whats_for_dinner_page(request: Request):
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)
    return templates.TemplateResponse(request, "whats_for_dinner.html", {
        "request": request,
        "nav_page": "meals",
        "auth": auth,
    })


@app.post("/api/suggest-meals")
async def suggest_meals(request: Request):
    """Suggest meals based on pantry inventory."""
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    family_id = auth["family_id"]
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "API key not configured"}, status_code=500)

    pantry_rows = db.get_pantry_items(family_id)
    today = date.today()
    pantry_with_expiry = []
    for r in pantry_rows:
        days_left = None
        exp = r.get("expires_at") if isinstance(r, dict) else r["expires_at"]
        if exp:
            try:
                days_left = (date.fromisoformat(exp) - today).days
            except Exception:
                days_left = None
        pantry_with_expiry.append({"name": r["text"], "days_left": days_left})
    pantry_items = [e["name"] for e in pantry_with_expiry]

    form = await request.form()
    preferences = form.get("preferences", "")

    try:
        result = suggest_meals_from_pantry(pantry_with_expiry, preferences, api_key)
        expiring_soon = [
            {"name": e["name"], "days_left": e["days_left"]}
            for e in pantry_with_expiry
            if e["days_left"] is not None and e["days_left"] <= 7
        ]
        expiring_soon.sort(key=lambda e: e["days_left"])
        return JSONResponse({
            "ok": True, **result,
            "pantry_items": pantry_items,
            "expiring_soon": expiring_soon,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/suggest-meals-from-photo")
async def suggest_meals_photo(request: Request, file: UploadFile = File(...)):
    """Identify fridge contents and suggest meals."""
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "API key not configured"}, status_code=500)

    form = await request.form()
    preferences = form.get("preferences", "")

    # Save uploaded photo
    upload_dir = Path("uploads")
    upload_dir.mkdir(exist_ok=True)
    ext = Path(file.filename or "photo.jpg").suffix or ".jpg"
    save_path = upload_dir / f"fridge_{uuid4().hex[:8]}{ext}"
    content = await file.read()
    save_path.write_bytes(content)

    try:
        result = suggest_meals_from_photo(str(save_path), api_key, preferences)
        # Also update pantry with identified items
        family_id = auth["family_id"]
        identified = result.get("identified", [])
        if identified:
            pantry = None
            for l in db.get_lists(family_id):
                if l["name"].lower() in ("pantry", "inventory", "home inventory"):
                    pantry = l
                    break
            if not pantry:
                pantry_id = str(uuid4())
                db.create_list(pantry_id, family_id, "Pantry", icon="🏠")
                pantry = {"id": pantry_id}
            # Get existing pantry items to avoid duplicates
            existing = {li["text"].lower().strip() for li in db.get_list_items(pantry["id"]) if not li["checked"]}
            added_to_pantry = 0
            for item_text in identified:
                if item_text.lower().strip() not in existing:
                    db.add_list_item(
                        str(uuid4()), pantry["id"], item_text,
                        added_by="Fridge Scan",
                        expires_at=estimate_expiry_date(item_text),
                    )
                    added_to_pantry += 1
            result["added_to_pantry"] = added_to_pantry
        return JSONResponse({"ok": True, **result})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        save_path.unlink(missing_ok=True)


@app.post("/api/add-missing-to-list")
async def add_missing_to_list(request: Request,
                               items_json: str = Form("[]"),
                               list_id: str = Form("")):
    """Add missing meal ingredients to a grocery list."""
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    family_id = auth["family_id"]
    items = json.loads(items_json)

    if not list_id:
        # Find or create Groceries list
        for l in db.get_lists(family_id):
            if l["name"].lower() in ("groceries", "grocery list", "grocery"):
                list_id = l["id"]
                break
        if not list_id:
            list_id = str(uuid4())
            db.create_list(list_id, family_id, "Groceries", icon="🛒")

    added = 0
    for item_text in items:
        if item_text.strip():
            db.add_list_item(str(uuid4()), list_id, item_text.strip(), added_by="Meal Suggestion")
            added += 1

    return JSONResponse({"ok": True, "count": added, "list_id": list_id})


# ── Receipt scanning ──

@app.post("/api/scan-receipt")
async def scan_receipt(request: Request, file: UploadFile = File(...)):
    """Extract items and prices from a receipt photo."""
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "API key not configured"}, status_code=500)

    upload_dir = Path("uploads")
    upload_dir.mkdir(exist_ok=True)
    ext = Path(file.filename or "receipt.jpg").suffix or ".jpg"
    save_path = upload_dir / f"receipt_{uuid4().hex[:8]}{ext}"
    content = await file.read()
    save_path.write_bytes(content)

    try:
        result = extract_receipt_items(str(save_path), api_key)
        return JSONResponse({"ok": True, **result})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        save_path.unlink(missing_ok=True)


@app.post("/api/apply-receipt")
async def apply_receipt(request: Request,
                         list_id: str = Form(""),
                         items_json: str = Form("[]")):
    """Match receipt items to a list, check them off, set prices, stock pantry."""
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    family_id = auth["family_id"]
    receipt_items = json.loads(items_json)

    # If no list specified, find the most recent grocery-type list
    if not list_id:
        for l in db.get_lists(family_id):
            if l["name"].lower() in ("groceries", "grocery list", "grocery", "meal plan groceries"):
                list_id = l["id"]
                break

    matched = 0
    unmatched = []
    if list_id:
        list_items = db.get_list_items(list_id)
        unchecked = {i["text"].lower().strip(): i for i in list_items if not i["checked"]}

        for ri in receipt_items:
            name = ri.get("name", "").lower().strip()
            price = ri.get("price", 0)
            # Try to match by substring
            found = None
            for key, li in unchecked.items():
                if name in key or key in name:
                    found = li
                    break
            if found:
                db.check_list_item(found["id"])
                if price and price > 0:
                    db.update_list_item_price(found["id"], price)
                matched += 1
                del unchecked[found["text"].lower().strip()]
            else:
                unmatched.append(ri)

    # Stock the Pantry with everything from the receipt (matched + unmatched),
    # with coarse-estimated expiry dates. Skip non-food line items.
    added_to_pantry = 0
    pantry = None
    for l in db.get_lists(family_id):
        if l["name"].lower() in ("pantry", "inventory", "home inventory"):
            pantry = l
            break
    if pantry is None:
        pantry_id = str(uuid4())
        db.create_list(pantry_id, family_id, "Pantry", icon="🏠")
        pantry = {"id": pantry_id}

    existing = {li["text"].lower().strip() for li in db.get_list_items(pantry["id"]) if not li["checked"]}
    SKIP_TOKENS = ("tax", "subtotal", "total", "discount", "coupon", "savings", "fee", "tip")

    for ri in receipt_items:
        raw_name = (ri.get("name") or "").strip()
        if not raw_name:
            continue
        lname = raw_name.lower()
        if any(tok in lname for tok in SKIP_TOKENS):
            continue
        if lname in existing:
            continue
        expires_at = estimate_expiry_date(raw_name)
        new_id = str(uuid4())
        db.add_list_item(
            new_id,
            pantry["id"],
            raw_name,
            added_by="Receipt",
            quantity=max(1, int(ri.get("quantity") or 1)),
            expires_at=expires_at,
        )
        # Carry receipt price onto the pantry item so waste can be costed.
        try:
            price = float(ri.get("price") or 0)
            if price > 0:
                db.update_list_item_price(new_id, price)
        except (TypeError, ValueError):
            pass
        existing.add(lname)
        added_to_pantry += 1

    return JSONResponse({
        "ok": True,
        "matched": matched,
        "unmatched": unmatched,
        "list_id": list_id,
        "added_to_pantry": added_to_pantry,
    })


# ── Family Data Chat ──

@app.get("/ask", response_class=HTMLResponse)
async def ask_page(request: Request, q: str = ""):
    auth = _require_auth(request)
    if not auth:
        return RedirectResponse(url="/welcome", status_code=303)
    return templates.TemplateResponse(request, "ask.html", {
        "request": request, "nav_page": "home", "auth": auth,
        "prefill_query": q.strip(),
    })


@app.post("/api/ask")
async def ask_family_data(request: Request, question: str = Form(...)):
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse({"error": "API key not configured"}, status_code=500)

    family_data = db.get_family_data_summary(auth["family_id"])
    try:
        answer = answer_family_question(question, family_data, api_key)
        return JSONResponse({"ok": True, "answer": answer})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Voice / AI agent routes ──

@app.post("/api/voice-to-items")
async def voice_to_items(request: Request, text: str = Form(...)):
    """AI parses spoken text into individual list items."""
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        # Fallback: just split by commas/and
        items = [s.strip() for s in re.split(r',|\band\b', text) if s.strip()]
        return JSONResponse({"items": items})

    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        messages=[{"role": "user", "content": f"""Extract individual shopping/grocery items from this spoken text.
Return ONLY a JSON array of strings. No explanation.
If the text contains non-list items (like greetings or filler words), ignore them.
Example: "we need milk eggs and some bread oh and tomatoes" → ["Milk", "Eggs", "Bread", "Tomatoes"]

Spoken text: "{text}"
"""}],
    )
    try:
        from main import clean_json_response
        cleaned = clean_json_response(msg.content[0].text)
        items = json.loads(cleaned)
        if not isinstance(items, list):
            items = [text]
    except Exception:
        items = [s.strip() for s in re.split(r',|\band\b', text) if s.strip()]

    return JSONResponse({"items": items})


@app.post("/api/photo-to-items")
async def photo_to_items(request: Request, file: UploadFile = File(...)):
    """AI extracts list items from a photo (receipt, shelf, handwritten list)."""
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return JSONResponse({"error": "ANTHROPIC_API_KEY not set"}, status_code=500)

    import base64
    from anthropic import Anthropic

    image_data = await file.read()
    content_type = file.content_type or "image/jpeg"
    b64 = base64.standard_b64encode(image_data).decode("utf-8")

    client = Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": content_type, "data": b64}},
            {"type": "text", "text": """Look at this image and extract all items that would go on a shopping/grocery list.
This could be a receipt, a handwritten list, a photo of a fridge/pantry shelf, or food items.
Return ONLY a JSON array of strings — the item names. No explanation, no markdown.
Example: ["Milk", "Eggs", "Bread", "Tomatoes"]
If you can't identify specific items, return an empty array [].
"""},
        ]}],
    )
    try:
        from main import clean_json_response
        cleaned = clean_json_response(msg.content[0].text)
        items = json.loads(cleaned)
        if not isinstance(items, list):
            items = []
    except Exception:
        items = []

    return JSONResponse({"items": items})


@app.post("/api/voice-command")
async def voice_command(request: Request, text: str = Form(...)):
    """Universal voice command — AI decides what to do: add to list, create event, set reminder."""
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        # Fallback: try to parse simple "add X to Y" patterns
        return JSONResponse({"action": "unknown", "text": text})

    family_id = auth["family_id"]

    # Get family context for AI
    lists = db.get_lists(family_id)
    list_names = [{"id": l["id"], "name": l["name"]} for l in lists]

    chores = db.get_chores(family_id)
    chore_names = [{"id": c["id"], "title": c["title"], "assigned_to": c["assigned_to"] or "everyone"} for c in chores]

    members = db.get_family_members(family_id)
    member_names = [m["display_name"] for m in members]

    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        messages=[{"role": "user", "content": f"""You are FamPilot, a family assistant. Parse this voice command and decide what to do.

Family members: {json.dumps(member_names)}
Shopping lists: {json.dumps(list_names)}
Chores: {json.dumps(chore_names)}

Return ONLY a JSON object with one of these actions:

1. Add items to a list: {{"action": "add_to_list", "list_id": "...", "items": ["item1", "item2"]}}
   - If no matching list exists: {{"action": "create_list_and_add", "list_name": "...", "items": ["item1"]}}

2. Mark a chore as done: {{"action": "chore_done", "chore_id": "..."}}

3. Create a new chore: {{"action": "chore_create", "title": "...", "assigned_to": "...", "recurrence": "daily|weekly|none"}}
   - Match assigned_to to a family member name. Use "" for everyone.

4. Create a calendar event/task/reminder: {{"action": "create_event", "text": "the original text"}}

5. Generate a meal plan: {{"action": "generate_meal_plan", "days": 7, "preferences": ""}}
   - Default to 7 days if not specified.
   - Include any dietary preferences mentioned (e.g. "vegetarian", "no dairy").

6. Add to home inventory/pantry: {{"action": "add_to_inventory", "items": [{{"name": "item1", "qty": 1}}, {{"name": "item2", "qty": 3}}]}}
   - Use this when someone says what they HAVE at home, not what they NEED to buy.
   - Include quantities when mentioned. Default qty to 1 if not specified.
   - "I have rice, chicken, and tomatoes" → items: [{{"name":"rice","qty":1}},{{"name":"chicken","qty":1}},{{"name":"tomatoes","qty":1}}]
   - "We have 2 dozen eggs" → items: [{{"name":"eggs","qty":24}}]
   - "I have tomatoes (3) dry beans (4)" → items: [{{"name":"tomatoes","qty":3}},{{"name":"dry beans","qty":4}}]

7. Not sure: {{"action": "unknown", "text": "the original text"}}

Be smart. "Mark dishes done" matches a chore with "dishes" in the title.
"Add milk to groceries" matches a list named "Groceries".
"Assign laundry to Alex" creates a chore assigned to Alex.
"We need tomatoes" adds to a grocery-type list if one exists.
"Dentist Tuesday 3pm" creates a calendar event.
"Plan meals for the week" generates a meal plan.
"Make a 5 day vegetarian meal plan" generates a 5-day vegetarian meal plan.
"I have rice and chicken" adds to home inventory.
"We have milk, eggs, and bread" adds to home inventory.

Voice command: "{text}"
"""}],
    )
    try:
        from main import clean_json_response
        cleaned = clean_json_response(msg.content[0].text)
        result = json.loads(cleaned)
    except Exception:
        result = {"action": "unknown", "text": text}

    # Execute the action
    if result.get("action") == "add_to_list":
        list_id = result.get("list_id")
        items = result.get("items", [])
        lst = db.get_list(list_id) if list_id else None
        if lst and lst["family_id"] == family_id:
            added = []
            for item_text in items:
                item_id = str(uuid4())
                db.add_list_item(item_id, list_id, item_text, added_by=auth["display_name"])
                added.append({"id": item_id, "text": item_text})
            items_text = ", ".join(a["text"] for a in added[:3])
            db.log_activity(family_id, auth["display_name"], f"added {items_text} to {lst['name']}", "list_item_added")
            return JSONResponse({
                "action": "added_to_list",
                "list_name": lst["name"],
                "list_id": list_id,
                "items": added,
            })

    elif result.get("action") == "create_list_and_add":
        list_name = result.get("list_name", "Shopping List")
        items = result.get("items", [])
        list_id = str(uuid4())
        db.create_list(list_id, family_id, list_name)
        added = []
        for item_text in items:
            item_id = str(uuid4())
            db.add_list_item(item_id, list_id, item_text, added_by=auth["display_name"])
            added.append({"id": item_id, "text": item_text})
        db.log_activity(family_id, auth["display_name"], f"created \"{list_name}\" with {len(added)} items", "list_created")
        return JSONResponse({
            "action": "created_list_and_added",
            "list_name": list_name,
            "list_id": list_id,
            "items": added,
        })

    elif result.get("action") == "chore_done":
        chore_id = result.get("chore_id")
        chore = db.get_chore(chore_id) if chore_id else None
        if chore and chore["family_id"] == family_id:
            today_str = _local_today().isoformat()
            log_id = str(uuid4())
            db.log_chore_done(log_id, chore_id, auth["display_name"], today_str)
            streak = db.get_chore_streak(chore_id, today_str)
            db.log_activity(family_id, auth["display_name"], f"completed \"{chore['title']}\"", "chore_done")
            return JSONResponse({
                "action": "chore_done",
                "chore_title": chore["title"],
                "streak": streak,
            })

    elif result.get("action") == "chore_create":
        title = result.get("title", "").strip()
        if title:
            chore_id = str(uuid4())
            assigned = result.get("assigned_to", "")
            recurrence = result.get("recurrence", "daily")
            db.create_chore(chore_id, family_id, title,
                            assigned_to=assigned, recurrence=recurrence)
            db.log_activity(family_id, auth["display_name"], f"created chore \"{title}\"", "chore_created")
            return JSONResponse({
                "action": "chore_created",
                "title": title,
                "assigned_to": assigned,
            })

    elif result.get("action") == "generate_meal_plan":
        days = result.get("days", 7)
        preferences = result.get("preferences", "")
        members = db.get_family_members(family_id)
        member_count = len(members)

        meal_client = Anthropic(api_key=api_key)
        meal_msg = meal_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            messages=[{"role": "user", "content": f"""Generate a {days}-day family meal plan.

Family size: {member_count} people
Preferences/restrictions: {preferences or 'None specified'}

Return ONLY a JSON object with this structure:
{{
  "days": [
    {{
      "day": "Monday",
      "breakfast": {{"name": "...", "time": "15 min"}},
      "lunch": {{"name": "...", "time": "20 min"}},
      "dinner": {{"name": "...", "time": "30 min"}},
      "snack": {{"name": "..."}}
    }}
  ],
  "grocery_list": ["item 1", "item 2", ...]
}}

Rules:
- Practical, family-friendly meals (not fancy restaurant dishes)
- Vary cuisines across the week
- Include prep time estimates
- The grocery list should include ALL ingredients needed, organized logically
- Assume a basic pantry (salt, pepper, oil, common spices already available)
- Keep it realistic for busy parents
"""}],
        )
        try:
            from main import clean_json_response
            cleaned = clean_json_response(meal_msg.content[0].text)
            meals = json.loads(cleaned)
            plan_id = str(uuid4())
            db.save_meal_plan(plan_id, family_id, json.dumps(meals),
                              days=days, preferences=preferences)
            return JSONResponse({
                "action": "meal_plan_generated",
                "plan_id": plan_id,
                "days": days,
                "redirect": "/meals",
            })
        except Exception:
            return JSONResponse({
                "action": "error",
                "message": "Failed to generate meal plan. Please try again.",
            })

    elif result.get("action") == "add_to_inventory":
        raw_items = result.get("items", [])
        if raw_items:
            # Find or create a Pantry list
            pantry = None
            for l in lists:
                if l["name"].lower() in ("pantry", "inventory", "home inventory"):
                    pantry = l
                    break
            if not pantry:
                pantry_id = str(uuid4())
                db.create_list(pantry_id, family_id, "Pantry", icon="🏠")
                pantry = {"id": pantry_id, "name": "Pantry"}
            added = []
            for item in raw_items:
                # Support both old format (string) and new format (dict with name/qty)
                if isinstance(item, dict):
                    item_text = item.get("name", "")
                    qty = int(item.get("qty", 1))
                else:
                    item_text = str(item)
                    qty = 1
                if not item_text:
                    continue
                item_id = str(uuid4())
                db.add_list_item(item_id, pantry["id"], item_text, added_by=auth["display_name"], quantity=qty)
                added.append({"id": item_id, "text": item_text, "quantity": qty})
            items_text = ", ".join(a["text"] for a in added[:3])
            db.log_activity(family_id, auth["display_name"], f"added {items_text} to Pantry", "inventory_added")
            return JSONResponse({
                "action": "added_to_inventory",
                "list_name": pantry["name"],
                "list_id": pantry["id"],
                "items": added,
            })

    elif result.get("action") == "create_event":
        return JSONResponse({
            "action": "create_event",
            "text": result.get("text", text),
            "redirect": f"/process-text",
        })

    return JSONResponse({"action": "unknown", "text": text})


# ── Push Notifications ──

@app.get("/api/push/vapid-key", response_class=JSONResponse)
async def get_vapid_key():
    return JSONResponse({"key": os.getenv("VAPID_PUBLIC_KEY", "")})


@app.post("/api/push/subscribe")
async def push_subscribe(request: Request):
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    sub = body.get("subscription") or {}
    endpoint = sub.get("endpoint")
    keys = sub.get("keys") or {}
    p256dh = keys.get("p256dh")
    auth_key = keys.get("auth")
    if not (endpoint and p256dh and auth_key):
        return JSONResponse({"error": "invalid subscription"}, status_code=400)
    db.save_push_subscription(
        endpoint=endpoint,
        device_id=auth["device_id"],
        member_id=auth["member_id"],
        family_id=auth["family_id"],
        p256dh=p256dh,
        auth=auth_key,
        user_agent=request.headers.get("user-agent", ""),
    )
    return JSONResponse({"ok": True})


@app.delete("/api/push/subscribe")
async def push_unsubscribe(request: Request):
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    endpoint = body.get("endpoint")
    if endpoint:
        db.delete_push_subscription(endpoint)
    return JSONResponse({"ok": True})


@app.post("/api/push/test")
async def push_test(request: Request):
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    count = push_module.send_to_member(
        auth["member_id"],
        title="FamPilot",
        body=f"Hello {auth['display_name']}! Notifications are working.",
        url="/",
    )
    return JSONResponse({"ok": True, "sent": count})


@app.post("/api/suggestion/accept")
async def accept_suggestion(request: Request,
                             list_id: str = Form(...),
                             text: str = Form(...)):
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    lst = db.get_list(list_id)
    if not lst or lst["family_id"] != auth["family_id"]:
        return JSONResponse({"error": "not found"}, status_code=404)
    item_id = str(uuid4())
    db.add_list_item(item_id, list_id, text, added_by=auth["display_name"])
    db.log_activity(auth["family_id"], auth["display_name"], f"added {text} to {lst['name']}", "list_item_added")
    return JSONResponse({"ok": True, "list_name": lst["name"]})


# ── Briefing & Recap API ──

@app.post("/api/send-briefing")
async def send_briefing(request: Request):
    """Manually trigger morning briefing for the current family."""
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    briefing = db.build_morning_briefing(auth["family_id"])
    sent = push_module.send_to_family(
        auth["family_id"], title=briefing["title"], body=briefing["body"],
        url="/", tag="morning-briefing")
    return JSONResponse({"ok": True, "sent": sent, **briefing})


@app.post("/api/send-recap")
async def send_recap(request: Request):
    """Manually trigger weekly recap for the current family."""
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    recap = db.build_weekly_recap(auth["family_id"])
    sent = push_module.send_to_family(
        auth["family_id"], title=recap["title"], body=recap["body"],
        url="/", tag="weekly-recap")
    return JSONResponse({"ok": True, "sent": sent, **recap})


@app.get("/api/preview-briefing")
async def preview_briefing(request: Request):
    """Preview what the morning briefing would say (no push sent)."""
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    briefing = db.build_morning_briefing(auth["family_id"])
    return JSONResponse(briefing)


@app.get("/api/preview-recap")
async def preview_recap(request: Request):
    """Preview what the weekly recap would say (no push sent)."""
    auth = _require_auth(request)
    if not auth:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    recap = db.build_weekly_recap(auth["family_id"])
    return JSONResponse(recap)


# ── Admin stats (password-protected) ──

@app.get("/admin/stats", response_class=JSONResponse)
async def admin_stats(request: Request, key: str = ""):
    admin_key = os.getenv("ADMIN_KEY", "")
    if not admin_key or key != admin_key:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    counts = {}
    for table in ["families", "members", "devices", "items", "lists", "chores"]:
        row = db._execute(f"SELECT COUNT(*) as cnt FROM {table}", fetch="one")
        counts[table] = row["cnt"] if isinstance(row, dict) else row[0]

    families = db._execute("SELECT id, name, created_at FROM families ORDER BY created_at DESC", fetch="all")
    family_list = []
    for f in families:
        members = db._execute(
            "SELECT m.display_name, d.last_seen FROM members m LEFT JOIN devices d ON d.member_id = m.id WHERE m.family_id = ? ORDER BY d.last_seen DESC",
            (f["id"],), fetch="all"
        )
        family_list.append({
            "name": f["name"],
            "created": str(f["created_at"]),
            "members": [{"name": m["display_name"], "last_seen": str(m["last_seen"] or "")} for m in members],
        })

    # Active in last 24h / 7d
    active_24h = db._execute(
        "SELECT COUNT(DISTINCT member_id) as cnt FROM devices WHERE last_seen > ?",
        ((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),), fetch="one"
    )
    active_7d = db._execute(
        "SELECT COUNT(DISTINCT member_id) as cnt FROM devices WHERE last_seen > ?",
        ((datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),), fetch="one"
    )
    counts["active_last_24h"] = active_24h["cnt"] if isinstance(active_24h, dict) else active_24h[0]
    counts["active_last_7d"] = active_7d["cnt"] if isinstance(active_7d, dict) else active_7d[0]
    counts["family_details"] = family_list
    return JSONResponse(counts)


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
