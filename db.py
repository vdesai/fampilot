"""
FamPilot — Database persistence layer.

Supports both PostgreSQL (production) and SQLite (local dev).

If DATABASE_URL env var is set → uses PostgreSQL.
Otherwise falls back to SQLite at DB_PATH (default: fampilot.db).
"""

import os
import secrets
import string
import json
from datetime import datetime, date, timedelta, timezone
from typing import Optional

# ── Detect database backend ──

DATABASE_URL = os.getenv("DATABASE_URL")
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3


def _local_today() -> date:
    tz_name = os.getenv("APP_TIMEZONE")
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz_name)).date()
        except Exception:
            pass
    return date.today()


DB_PATH = os.getenv("DB_PATH", "fampilot.db")

# ── Connection helpers ──

def _pg_conn():
    con = psycopg2.connect(DATABASE_URL)
    con.autocommit = False
    return con


def _sq_conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


class _DictRow(dict):
    """Make Postgres rows behave like sqlite3.Row (subscriptable)."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _execute(sql, params=(), fetch=None, returning=False):
    """Execute SQL against whichever backend is active.
    fetch: None (no return), 'one', 'all'
    """
    if USE_POSTGRES:
        # Convert ? placeholders to %s for psycopg2
        sql = sql.replace('?', '%s')
        con = _pg_conn()
        try:
            cur = con.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params)
            if fetch == 'one':
                row = cur.fetchone()
                con.commit()
                return _DictRow(row) if row else None
            elif fetch == 'all':
                rows = cur.fetchall()
                con.commit()
                return [_DictRow(r) for r in rows]
            else:
                con.commit()
                return None
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
    else:
        con = _sq_conn()
        try:
            if fetch == 'one':
                row = con.execute(sql, params).fetchone()
                return row
            elif fetch == 'all':
                return con.execute(sql, params).fetchall()
            else:
                con.execute(sql, params)
                con.commit()
                return None
        finally:
            con.close()


def _execute_many(statements):
    """Execute multiple SQL statements (for schema creation)."""
    if USE_POSTGRES:
        con = _pg_conn()
        try:
            cur = con.cursor()
            for sql in statements:
                cur.execute(sql)
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
    else:
        con = _sq_conn()
        try:
            for sql in statements:
                con.execute(sql)
            con.commit()
        finally:
            con.close()


# ── Schema ──

_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS families (
        id         TEXT PRIMARY KEY,
        name       TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS members (
        id           TEXT PRIMARY KEY,
        family_id    TEXT NOT NULL REFERENCES families(id),
        display_name TEXT DEFAULT 'Family Member',
        role         TEXT DEFAULT 'member',
        created_at   TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS devices (
        id         TEXT PRIMARY KEY,
        member_id  TEXT NOT NULL REFERENCES members(id),
        last_seen  TEXT,
        user_agent TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS invite_codes (
        code       TEXT PRIMARY KEY,
        family_id  TEXT NOT NULL REFERENCES families(id),
        created_by TEXT,
        expires_at TEXT NOT NULL,
        max_uses   INTEGER DEFAULT 20,
        use_count  INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS items (
        id                  TEXT PRIMARY KEY,
        created_at          TEXT NOT NULL,
        type                TEXT,
        confidence          REAL,
        reasoning           TEXT,
        title               TEXT,
        start_date          TEXT,
        end_date            TEXT,
        time                TEXT,
        location            TEXT,
        notes               TEXT,
        priority            TEXT,
        remind_at           TEXT,
        original_input_text TEXT,
        uploaded_image_path TEXT,
        reminder_time       TEXT,
        reminder_sent       INTEGER DEFAULT 0,
        reminder_triggered_at TEXT,
        group_id            TEXT,
        group_title         TEXT,
        group_summary       TEXT,
        completed           INTEGER DEFAULT 0,
        family_id           TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS lists (
        id          TEXT PRIMARY KEY,
        family_id   TEXT NOT NULL REFERENCES families(id),
        name        TEXT NOT NULL,
        icon        TEXT DEFAULT '🛒',
        created_at  TEXT NOT NULL,
        archived    INTEGER DEFAULT 0,
        share_token TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS list_items (
        id          TEXT PRIMARY KEY,
        list_id     TEXT NOT NULL REFERENCES lists(id),
        text        TEXT NOT NULL,
        checked     INTEGER DEFAULT 0,
        added_by    TEXT,
        created_at  TEXT NOT NULL,
        sort_order  INTEGER DEFAULT 0,
        quantity    INTEGER DEFAULT 1,
        note        TEXT,
        assigned_to TEXT,
        price       REAL
    )""",
    """CREATE TABLE IF NOT EXISTS chores (
        id          TEXT PRIMARY KEY,
        family_id   TEXT NOT NULL REFERENCES families(id),
        title       TEXT NOT NULL,
        icon        TEXT DEFAULT '🧹',
        assigned_to TEXT,
        recurrence  TEXT DEFAULT 'none',
        created_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS chore_log (
        id          TEXT PRIMARY KEY,
        chore_id    TEXT NOT NULL REFERENCES chores(id),
        done_by     TEXT,
        done_date   TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS meal_plans (
        id          TEXT PRIMARY KEY,
        family_id   TEXT NOT NULL REFERENCES families(id),
        created_at  TEXT NOT NULL,
        days        INTEGER DEFAULT 7,
        preferences TEXT,
        meals_json  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS activity_log (
        id          TEXT PRIMARY KEY,
        family_id   TEXT NOT NULL,
        member_name TEXT NOT NULL,
        action      TEXT NOT NULL,
        action_type TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS push_subscriptions (
        endpoint        TEXT PRIMARY KEY,
        device_id       TEXT NOT NULL,
        member_id       TEXT NOT NULL,
        family_id       TEXT NOT NULL,
        p256dh          TEXT NOT NULL,
        auth            TEXT NOT NULL,
        user_agent      TEXT,
        created_at      TEXT NOT NULL,
        last_success_at TEXT,
        failure_count   INTEGER DEFAULT 0
    )""",
]


def init_db() -> None:
    _execute_many(_SCHEMA)
    # Migrate: add new columns if missing
    for col, definition in [
        ("quantity", "INTEGER DEFAULT 1"),
        ("note", "TEXT"),
        ("assigned_to", "TEXT"),
        ("price", "REAL"),
    ]:
        try:
            _execute(f"ALTER TABLE list_items ADD COLUMN {col} {definition}")
        except Exception:
            pass
    # Lists table migrations
    try:
        _execute("ALTER TABLE lists ADD COLUMN share_token TEXT")
    except Exception:
        pass


# ── Settings ──

def get_setting(key: str) -> Optional[str]:
    row = _execute("SELECT value FROM settings WHERE key=?", (key,), fetch='one')
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    if USE_POSTGRES:
        _execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, value),
        )
    else:
        _execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))


# ── Families, members, devices ──

def create_family(family_id: str, name: str) -> None:
    _execute(
        "INSERT INTO families (id, name, created_at) VALUES (?,?,?)",
        (family_id, name, datetime.now(timezone.utc).isoformat()),
    )


def get_family(family_id: str) -> Optional[dict]:
    return _execute("SELECT * FROM families WHERE id=?", (family_id,), fetch='one')


def create_member(member_id: str, family_id: str, display_name: str, role: str = "member") -> None:
    _execute(
        "INSERT INTO members (id, family_id, display_name, role, created_at) VALUES (?,?,?,?,?)",
        (member_id, family_id, display_name, role, datetime.now(timezone.utc).isoformat()),
    )


def get_member(member_id: str) -> Optional[dict]:
    return _execute("SELECT * FROM members WHERE id=?", (member_id,), fetch='one')


def create_device(device_id: str, member_id: str, user_agent: str = "") -> None:
    _execute(
        "INSERT INTO devices (id, member_id, last_seen, user_agent) VALUES (?,?,?,?)",
        (device_id, member_id, datetime.now(timezone.utc).isoformat(), user_agent),
    )


def get_device(device_id: str) -> Optional[dict]:
    return _execute("SELECT * FROM devices WHERE id=?", (device_id,), fetch='one')


def touch_device(device_id: str) -> None:
    _execute(
        "UPDATE devices SET last_seen=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), device_id),
    )


def resolve_device(device_id: str) -> Optional[dict]:
    row = _execute(
        """SELECT d.id AS device_id, d.member_id,
                  m.family_id, m.display_name, m.role,
                  f.name AS family_name
           FROM devices d
           JOIN members m ON m.id = d.member_id
           JOIN families f ON f.id = m.family_id
           WHERE d.id = ?""",
        (device_id,),
        fetch='one',
    )
    return dict(row) if row else None


def get_family_members(family_id: str) -> list:
    return _execute(
        "SELECT * FROM members WHERE family_id=? ORDER BY created_at",
        (family_id,), fetch='all',
    )


# ── Invite codes ──

def _generate_code() -> str:
    chars = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(chars) for _ in range(6))


def create_invite_code(family_id: str, created_by: str) -> str:
    code = _generate_code()
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    _execute(
        "INSERT INTO invite_codes (code, family_id, created_by, expires_at) VALUES (?,?,?,?)",
        (code, family_id, created_by, expires),
    )
    return code


def get_invite_code(code: str) -> Optional[dict]:
    return _execute("SELECT * FROM invite_codes WHERE code=?", (code.upper(),), fetch='one')


def use_invite_code(code: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    row = _execute("SELECT * FROM invite_codes WHERE code=?", (code.upper(),), fetch='one')
    if not row:
        return False
    if row["expires_at"] < now:
        return False
    if row["use_count"] >= row["max_uses"]:
        return False
    _execute(
        "UPDATE invite_codes SET use_count = use_count + 1 WHERE code=?",
        (code.upper(),),
    )
    return True


def get_active_invite_code(family_id: str) -> Optional[str]:
    now = datetime.now(timezone.utc).isoformat()
    row = _execute(
        """SELECT code FROM invite_codes
           WHERE family_id=? AND expires_at > ? AND use_count < max_uses
           ORDER BY expires_at DESC LIMIT 1""",
        (family_id, now),
        fetch='one',
    )
    return row["code"] if row else None


# ── Legacy family token ──

def get_or_create_family_token() -> str:
    token = get_setting("family_token")
    if not token:
        token = secrets.token_urlsafe(12)
        set_setting("family_token", token)
    return token


# ── Items ──

def get_family_week(family_id: str) -> list:
    today = _local_today()
    in7days = (today + timedelta(days=7)).isoformat()
    today = today.isoformat()
    return _execute(
        """SELECT * FROM items
           WHERE family_id = ? AND start_date BETWEEN ? AND ?
             AND (completed IS NULL OR completed = 0)
           ORDER BY start_date, time""",
        (family_id, today, in7days), fetch='all',
    )


def get_calendar_week(family_id: str, week_offset: int = 0) -> dict:
    """Get a full week of events grouped by date, for the calendar view."""
    today = _local_today()
    # Start from Monday of the target week
    start = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    end = start + timedelta(days=6)

    rows = _execute(
        """SELECT * FROM items
           WHERE family_id = ? AND start_date BETWEEN ? AND ?
           ORDER BY start_date, time""",
        (family_id, start.isoformat(), end.isoformat()), fetch='all',
    ) or []

    # Group by date
    days = {}
    for i in range(7):
        d = (start + timedelta(days=i))
        days[d.isoformat()] = {
            "date": d.isoformat(),
            "day_name": d.strftime("%A"),
            "day_short": d.strftime("%a"),
            "day_num": d.day,
            "month_short": d.strftime("%b"),
            "is_today": d == today,
            "events": [],
        }

    for row in rows:
        sd = row["start_date"]
        if sd in days:
            days[sd]["events"].append(dict(row))

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "week_label": f"{start.strftime('%b %d')} – {end.strftime('%b %d, %Y')}",
        "days": list(days.values()),
    }


def search_family(family_id: str, query: str) -> dict:
    """Search across items, lists, list_items, and chores."""
    q = f"%{query}%"
    results = {"events": [], "list_items": [], "chores": []}

    # Search items (events/tasks/reminders)
    rows = _execute(
        """SELECT * FROM items WHERE family_id = ? AND
           (title LIKE ? OR notes LIKE ? OR location LIKE ?)
           ORDER BY created_at DESC LIMIT 20""",
        (family_id, q, q, q), fetch='all') or []
    results["events"] = [dict(r) for r in rows]

    # Search list items
    rows = _execute(
        """SELECT li.*, l.name as list_name, l.icon as list_icon FROM list_items li
           JOIN lists l ON l.id = li.list_id
           WHERE l.family_id = ? AND (li.text LIKE ? OR li.note LIKE ?)
           ORDER BY li.created_at DESC LIMIT 20""",
        (family_id, q, q), fetch='all') or []
    results["list_items"] = [dict(r) for r in rows]

    # Search chores
    rows = _execute(
        """SELECT * FROM chores WHERE family_id = ? AND title LIKE ?
           ORDER BY created_at DESC LIMIT 10""",
        (family_id, q), fetch='all') or []
    results["chores"] = [dict(r) for r in rows]

    return results


def save_item(item_id: str, result: dict,
              source_text: Optional[str] = None,
              image_path: Optional[str] = None,
              family_id: Optional[str] = None) -> None:
    rtype = result.get("type")
    data = result.get("data", {})
    start_date = data.get("start_date") or (data.get("due_date") if rtype == "task" else None)

    if USE_POSTGRES:
        _execute(
            """INSERT INTO items
               (id, created_at, type, confidence, reasoning,
                title, start_date, end_date, time, location,
                notes, priority, remind_at,
                original_input_text, uploaded_image_path, family_id)
               VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s, %s,%s,%s)
               ON CONFLICT (id) DO UPDATE SET
                type=EXCLUDED.type, confidence=EXCLUDED.confidence, reasoning=EXCLUDED.reasoning,
                title=EXCLUDED.title, start_date=EXCLUDED.start_date""",
            (item_id, datetime.now(timezone.utc).isoformat(), rtype,
             result.get("confidence"), result.get("reasoning"),
             data.get("title"), start_date, data.get("end_date"),
             data.get("time"), data.get("location"), data.get("notes"),
             data.get("priority"), data.get("remind_at"),
             source_text, image_path, family_id),
        )
    else:
        _execute(
            """INSERT OR REPLACE INTO items
               (id, created_at, type, confidence, reasoning,
                title, start_date, end_date, time, location,
                notes, priority, remind_at,
                original_input_text, uploaded_image_path, family_id)
               VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?, ?,?,?)""",
            (item_id, datetime.now(timezone.utc).isoformat(), rtype,
             result.get("confidence"), result.get("reasoning"),
             data.get("title"), start_date, data.get("end_date"),
             data.get("time"), data.get("location"), data.get("notes"),
             data.get("priority"), data.get("remind_at"),
             source_text, image_path, family_id),
        )


def save_flat_item(item_id: str, flat: dict,
                   source_text: Optional[str] = None,
                   image_path: Optional[str] = None,
                   group_id: Optional[str] = None,
                   group_title: Optional[str] = None,
                   group_summary: Optional[str] = None,
                   family_id: Optional[str] = None) -> None:
    if USE_POSTGRES:
        _execute(
            """INSERT INTO items
               (id, created_at, type, confidence, reasoning,
                title, start_date, end_date, time, location,
                notes, priority, remind_at,
                original_input_text, uploaded_image_path,
                group_id, group_title, group_summary, family_id)
               VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s, %s,%s, %s,%s,%s,%s)
               ON CONFLICT (id) DO NOTHING""",
            (item_id, datetime.now(timezone.utc).isoformat(),
             flat.get("type"), flat.get("confidence"), flat.get("reasoning"),
             flat.get("title"), flat.get("start_date"), flat.get("end_date"),
             flat.get("time"), flat.get("location"), flat.get("notes"),
             flat.get("priority"), flat.get("remind_at"),
             source_text, image_path, group_id, group_title, group_summary, family_id),
        )
    else:
        _execute(
            """INSERT OR REPLACE INTO items
               (id, created_at, type, confidence, reasoning,
                title, start_date, end_date, time, location,
                notes, priority, remind_at,
                original_input_text, uploaded_image_path,
                group_id, group_title, group_summary, family_id)
               VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?, ?,?, ?,?,?,?)""",
            (item_id, datetime.now(timezone.utc).isoformat(),
             flat.get("type"), flat.get("confidence"), flat.get("reasoning"),
             flat.get("title"), flat.get("start_date"), flat.get("end_date"),
             flat.get("time"), flat.get("location"), flat.get("notes"),
             flat.get("priority"), flat.get("remind_at"),
             source_text, image_path, group_id, group_title, group_summary, family_id),
        )


def update_type(item_id: str, new_type: str) -> None:
    _execute("UPDATE items SET type=?, confidence=1.0, reasoning=? WHERE id=?",
             (new_type, f"Manually set to '{new_type}' by user.", item_id))


def update_event_data(item_id: str, data: dict) -> None:
    _execute("UPDATE items SET title=?, start_date=?, end_date=?, time=?, location=? WHERE id=?",
             (data.get("title"), data.get("start_date"), data.get("end_date"),
              data.get("time"), data.get("location"), item_id))


def update_item(item_id: str, rtype: str, fields: dict) -> None:
    start_date = fields.get("start_date") or fields.get("due_date")
    _execute(
        """UPDATE items SET title=?, start_date=?, end_date=?, time=?,
               location=?, notes=?, priority=?, remind_at=?,
               reminder_time=?, reminder_sent=0
           WHERE id=?""",
        (fields.get("title"), start_date, fields.get("end_date"),
         fields.get("time"), fields.get("location"), fields.get("notes"),
         fields.get("priority"), fields.get("remind_at"),
         fields.get("reminder_time") or None, item_id),
    )


def get_due_reminders() -> list:
    now = datetime.now(timezone.utc).isoformat()
    return _execute(
        """SELECT * FROM items
           WHERE reminder_time IS NOT NULL AND reminder_time <= ?
             AND (reminder_sent IS NULL OR reminder_sent = 0)""",
        (now,), fetch='all',
    )


def mark_reminder_sent(item_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    _execute("UPDATE items SET reminder_sent=1, reminder_triggered_at=? WHERE id=?", (now, item_id))


def get_recent_reminders(family_id: str, hours: int = 6) -> list:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return _execute(
        """SELECT * FROM items
           WHERE family_id = ? AND reminder_triggered_at IS NOT NULL
             AND reminder_triggered_at >= ?
             AND (reminder_sent IS NULL OR reminder_sent != 2)
           ORDER BY reminder_triggered_at DESC""",
        (family_id, cutoff), fetch='all',
    )


def dismiss_reminder(item_id: str) -> None:
    _execute("UPDATE items SET reminder_sent=2 WHERE id=?", (item_id,))


def get_upcoming_items(family_id: str) -> list:
    today = _local_today()
    in3days = (today + timedelta(days=3)).isoformat()
    today = today.isoformat()
    return _execute(
        """SELECT * FROM items
           WHERE family_id = ? AND start_date BETWEEN ? AND ?
             AND (completed IS NULL OR completed = 0)
           ORDER BY start_date, time""",
        (family_id, today, in3days), fetch='all',
    )


def complete_item(item_id: str) -> None:
    _execute("UPDATE items SET completed=1 WHERE id=?", (item_id,))


def uncomplete_item(item_id: str) -> None:
    _execute("UPDATE items SET completed=0 WHERE id=?", (item_id,))


def delete_item(item_id: str) -> None:
    _execute("DELETE FROM items WHERE id=?", (item_id,))


def get_history(family_id: str, limit: int = 100) -> list:
    return _execute(
        "SELECT * FROM items WHERE family_id=? ORDER BY created_at DESC LIMIT ?",
        (family_id, limit), fetch='all',
    )


def get_item(item_id: str) -> Optional[dict]:
    return _execute("SELECT * FROM items WHERE id=?", (item_id,), fetch='one')


def row_to_result(row) -> dict:
    rtype = row["type"]
    if rtype == "event":
        data = {"title": row["title"], "start_date": row["start_date"],
                "end_date": row["end_date"], "time": row["time"], "location": row["location"]}
    elif rtype == "task":
        data = {"title": row["title"], "due_date": row["start_date"],
                "priority": row["priority"], "notes": row["notes"]}
    else:
        data = {"title": row["title"], "remind_at": row["remind_at"], "notes": row["notes"]}
    return {
        "type": rtype,
        "confidence": row["confidence"] if row["confidence"] is not None else 1.0,
        "reasoning": row["reasoning"] or "",
        "data": data,
    }


# ── Shopping Lists ──

def create_list(list_id: str, family_id: str, name: str, icon: str = "🛒") -> None:
    _execute("INSERT INTO lists (id, family_id, name, icon, created_at) VALUES (?,?,?,?,?)",
             (list_id, family_id, name, icon, datetime.now(timezone.utc).isoformat()))


def get_lists(family_id: str) -> list:
    return _execute(
        "SELECT * FROM lists WHERE family_id=? AND (archived=0 OR archived IS NULL) ORDER BY created_at DESC",
        (family_id,), fetch='all')


def get_list(list_id: str) -> Optional[dict]:
    return _execute("SELECT * FROM lists WHERE id=?", (list_id,), fetch='one')


def get_or_create_share_token(list_id: str) -> str:
    row = _execute("SELECT share_token FROM lists WHERE id=?", (list_id,), fetch='one')
    if row and row.get("share_token"):
        return row["share_token"]
    token = secrets.token_urlsafe(8)
    _execute("UPDATE lists SET share_token=? WHERE id=?", (token, list_id))
    return token


def get_list_by_share_token(token: str) -> Optional[dict]:
    return _execute("SELECT * FROM lists WHERE share_token=?", (token,), fetch='one')


def delete_list(list_id: str) -> None:
    _execute("DELETE FROM list_items WHERE list_id=?", (list_id,))
    _execute("DELETE FROM lists WHERE id=?", (list_id,))


def add_list_item(item_id: str, list_id: str, text: str, added_by: str = "", quantity: int = 1) -> None:
    row = _execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM list_items WHERE list_id=?",
        (list_id,), fetch='one')
    sort_order = row["next_order"] if row else 0
    _execute(
        "INSERT INTO list_items (id, list_id, text, added_by, created_at, sort_order, quantity) VALUES (?,?,?,?,?,?,?)",
        (item_id, list_id, text, added_by, datetime.now(timezone.utc).isoformat(), sort_order, max(1, quantity)))


def update_list_item_quantity(item_id: str, quantity: int) -> None:
    _execute("UPDATE list_items SET quantity=? WHERE id=?", (max(1, quantity), item_id))


def update_list_item_note(item_id: str, note: str) -> None:
    _execute("UPDATE list_items SET note=? WHERE id=?", (note.strip() or None, item_id))


def update_list_item_assigned(item_id: str, assigned_to: str) -> None:
    _execute("UPDATE list_items SET assigned_to=? WHERE id=?", (assigned_to.strip() or None, item_id))


def update_list_item_price(item_id: str, price: float) -> None:
    _execute("UPDATE list_items SET price=? WHERE id=?", (price if price and price > 0 else None, item_id))


def get_list_spending(list_id: str) -> float:
    row = _execute(
        "SELECT COALESCE(SUM(price), 0) AS total FROM list_items WHERE list_id=? AND checked=1 AND price IS NOT NULL",
        (list_id,), fetch='one')
    return row["total"] if row else 0.0


def get_list_items(list_id: str) -> list:
    return _execute(
        "SELECT * FROM list_items WHERE list_id=? ORDER BY checked ASC, sort_order ASC",
        (list_id,), fetch='all')


def check_list_item(item_id: str) -> None:
    _execute("UPDATE list_items SET checked=1 WHERE id=?", (item_id,))


def uncheck_list_item(item_id: str) -> None:
    _execute("UPDATE list_items SET checked=0 WHERE id=?", (item_id,))


def delete_list_item(item_id: str) -> None:
    _execute("DELETE FROM list_items WHERE id=?", (item_id,))


def clear_checked_items(list_id: str) -> None:
    _execute("DELETE FROM list_items WHERE list_id=? AND checked=1", (list_id,))


def get_pantry_items(family_id: str) -> list:
    """Get unchecked items from Pantry/Inventory lists for smart deduction."""
    return _execute(
        """SELECT li.text FROM list_items li
           JOIN lists l ON l.id = li.list_id
           WHERE l.family_id = ? AND li.checked = 0
             AND LOWER(l.name) IN ('pantry', 'inventory', 'home inventory')""",
        (family_id,), fetch='all') or []


def get_stale_pantry_items(family_id: str, days: int = 14) -> list:
    """Get pantry items added more than `days` ago — likely running low."""
    cutoff = (_local_today() - timedelta(days=days)).isoformat()
    return _execute(
        """SELECT li.*, l.name as list_name, l.id as pantry_list_id FROM list_items li
           JOIN lists l ON l.id = li.list_id
           WHERE l.family_id = ? AND li.checked = 0
             AND LOWER(l.name) IN ('pantry', 'inventory', 'home inventory')
             AND li.created_at < ?
           ORDER BY li.created_at ASC""",
        (family_id, cutoff), fetch='all') or []


def get_list_summary(family_id: str) -> list:
    return _execute(
        """SELECT l.*,
                  COUNT(li.id) AS total_items,
                  COALESCE(SUM(CASE WHEN li.checked = 1 THEN 1 ELSE 0 END), 0) AS checked_items
           FROM lists l
           LEFT JOIN list_items li ON li.list_id = l.id
           WHERE l.family_id = ? AND (l.archived = 0 OR l.archived IS NULL)
           GROUP BY l.id, l.family_id, l.name, l.icon, l.created_at, l.archived
           ORDER BY l.created_at DESC""",
        (family_id,), fetch='all')


# ── Chores ──

def create_chore(chore_id: str, family_id: str, title: str,
                 icon: str = "🧹", assigned_to: str = "", recurrence: str = "none") -> None:
    _execute(
        "INSERT INTO chores (id, family_id, title, icon, assigned_to, recurrence, created_at) VALUES (?,?,?,?,?,?,?)",
        (chore_id, family_id, title, icon, assigned_to, recurrence, datetime.now(timezone.utc).isoformat()))


def get_chores(family_id: str) -> list:
    return _execute("SELECT * FROM chores WHERE family_id=? ORDER BY created_at",
                    (family_id,), fetch='all')


def get_chore(chore_id: str) -> Optional[dict]:
    return _execute("SELECT * FROM chores WHERE id=?", (chore_id,), fetch='one')


def delete_chore(chore_id: str) -> None:
    _execute("DELETE FROM chore_log WHERE chore_id=?", (chore_id,))
    _execute("DELETE FROM chores WHERE id=?", (chore_id,))


def log_chore_done(log_id: str, chore_id: str, done_by: str, done_date: str) -> None:
    _execute(
        "INSERT INTO chore_log (id, chore_id, done_by, done_date, created_at) VALUES (?,?,?,?,?)",
        (log_id, chore_id, done_by, done_date, datetime.now(timezone.utc).isoformat()))


def undo_chore_done(chore_id: str, done_date: str) -> None:
    _execute("DELETE FROM chore_log WHERE chore_id=? AND done_date=?", (chore_id, done_date))


def get_chores_with_status(family_id: str, target_date: str) -> list:
    return _execute(
        """SELECT c.*,
                  CASE WHEN cl.id IS NOT NULL THEN 1 ELSE 0 END AS done_today,
                  cl.done_by
           FROM chores c
           LEFT JOIN chore_log cl ON cl.chore_id = c.id AND cl.done_date = ?
           WHERE c.family_id = ?
           ORDER BY c.assigned_to, c.title""",
        (target_date, family_id), fetch='all')


def get_chore_streak(chore_id: str, today: str) -> int:
    rows = _execute(
        "SELECT DISTINCT done_date FROM chore_log WHERE chore_id=? ORDER BY done_date DESC",
        (chore_id,), fetch='all')
    if not rows:
        return 0
    dates = [r["done_date"] for r in rows]
    streak = 0
    check = today
    while check in dates:
        streak += 1
        d = date.fromisoformat(check)
        check = (d - timedelta(days=1)).isoformat()
    return streak


# ── Meal Plans ──

def save_meal_plan(plan_id: str, family_id: str, meals_json: str,
                   days: int = 7, preferences: str = "") -> None:
    _execute(
        "INSERT INTO meal_plans (id, family_id, created_at, days, preferences, meals_json) VALUES (?,?,?,?,?,?)",
        (plan_id, family_id, datetime.now(timezone.utc).isoformat(), days, preferences, meals_json))


def get_latest_meal_plan(family_id: str) -> Optional[dict]:
    return _execute(
        "SELECT * FROM meal_plans WHERE family_id=? ORDER BY created_at DESC LIMIT 1",
        (family_id,), fetch='one')


def get_meal_plan(plan_id: str) -> Optional[dict]:
    return _execute("SELECT * FROM meal_plans WHERE id=?", (plan_id,), fetch='one')


def delete_meal_plan(plan_id: str) -> None:
    _execute("DELETE FROM meal_plans WHERE id=?", (plan_id,))


# ── Usage tracking (freemium) ──

FREE_SCAN_LIMIT = 5

def get_scan_count(family_id: str) -> int:
    month_start = _local_today().replace(day=1).isoformat()
    row = _execute(
        "SELECT COUNT(*) AS cnt FROM items WHERE family_id = ? AND created_at >= ?",
        (family_id, month_start), fetch='one')
    return row["cnt"] if row else 0


def can_scan(family_id: str) -> bool:
    premium = get_setting(f"premium:{family_id}")
    if premium == "1":
        return True
    return get_scan_count(family_id) < FREE_SCAN_LIMIT


def get_usage_info(family_id: str) -> dict:
    premium = get_setting(f"premium:{family_id}") == "1"
    count = get_scan_count(family_id)
    return {
        "premium": premium,
        "scans_used": count,
        "scans_limit": FREE_SCAN_LIMIT,
        "scans_remaining": max(0, FREE_SCAN_LIMIT - count) if not premium else 999,
        "can_scan": premium or count < FREE_SCAN_LIMIT,
    }


# ── Morning Briefing & Weekly Recap ──

def build_morning_briefing(family_id: str) -> dict:
    """Build the morning briefing content for push notification."""
    today = _local_today()
    today_str = today.isoformat()
    in3days = (today + timedelta(days=3)).isoformat()

    # Today's events
    today_items = _execute(
        """SELECT * FROM items WHERE family_id = ? AND start_date = ?
           AND (completed IS NULL OR completed = 0) ORDER BY time""",
        (family_id, today_str), fetch='all') or []

    # Pending chores
    chores = get_chores_with_status(family_id, today_str)
    pending_chores = [c for c in chores if not c.get("done_today")]

    # Stale pantry
    stale = get_stale_pantry_items(family_id)

    # Total list items
    total_unchecked = 0
    for lst in get_lists(family_id):
        items = get_list_items(lst["id"])
        total_unchecked += sum(1 for i in items if not i.get("checked"))

    # Build title and body
    parts = []
    if today_items:
        if len(today_items) == 1:
            t = today_items[0]
            time_str = f" at {t['time']}" if t.get('time') else ""
            parts.append(f"1 event today: {t['title']}{time_str}")
        else:
            parts.append(f"{len(today_items)} events today")

    if pending_chores:
        names = ", ".join(c["title"] for c in pending_chores[:2])
        if len(pending_chores) > 2:
            names += f" +{len(pending_chores) - 2} more"
        parts.append(f"Chores: {names}")

    if stale:
        names = ", ".join(s["text"] for s in stale[:3])
        parts.append(f"Running low: {names}")

    if total_unchecked > 0:
        parts.append(f"{total_unchecked} items on lists")

    if not parts:
        parts.append("All clear today!")

    body = ". ".join(parts)
    if not body.endswith((".", "!", "?")):
        body += "."
    title = f"Good morning! {parts[0]}" if parts else "Good morning!"

    return {"title": title, "body": body}


def build_weekly_recap(family_id: str) -> dict:
    """Build the weekly recap content."""
    today = _local_today()
    week_ago = (today - timedelta(days=7)).isoformat()

    # Spending this week
    total_spent = 0
    for lst in get_lists(family_id):
        rows = _execute(
            """SELECT COALESCE(SUM(price), 0) AS total FROM list_items
               WHERE list_id = ? AND checked = 1 AND price IS NOT NULL AND created_at >= ?""",
            (lst["id"], week_ago), fetch='one')
        if rows:
            total_spent += rows["total"]

    # Chores completed this week
    chores_done = _execute(
        """SELECT COUNT(*) AS cnt FROM chore_log cl
           JOIN chores c ON c.id = cl.chore_id
           WHERE c.family_id = ? AND cl.done_date >= ?""",
        (family_id, week_ago), fetch='one')
    chores_count = chores_done["cnt"] if chores_done else 0

    # Total chores available
    total_chores = len(get_chores(family_id))
    chore_total_possible = total_chores * 7 if total_chores > 0 else 0

    # Items added to lists this week
    items_added = _execute(
        """SELECT COUNT(*) AS cnt FROM list_items li
           JOIN lists l ON l.id = li.list_id
           WHERE l.family_id = ? AND li.created_at >= ?""",
        (family_id, week_ago), fetch='one')
    items_count = items_added["cnt"] if items_added else 0

    # Best chore streak
    best_streak = 0
    best_chore = ""
    for c in get_chores(family_id):
        streak = get_chore_streak(c["id"], today.isoformat())
        if streak > best_streak:
            best_streak = streak
            best_chore = c["title"]

    # Build body
    parts = []
    if total_spent > 0:
        parts.append(f"Spent ${total_spent:.2f} on groceries")
    if chores_count > 0:
        parts.append(f"{chores_count} chores completed")
    if items_count > 0:
        parts.append(f"{items_count} items tracked")
    if best_streak >= 3:
        parts.append(f"Best streak: {best_chore} ({best_streak} days)")

    if not parts:
        parts.append("Quiet week! Time to get organized?")

    body = ". ".join(parts)
    if not body.endswith((".", "!", "?")):
        body += "."
    title = "Your week in review"

    return {"title": title, "body": body}


def get_all_family_ids() -> list:
    """Get all family IDs for batch operations like morning briefing."""
    rows = _execute("SELECT id FROM families", fetch='all') or []
    return [r["id"] for r in rows]


# ── Family Data Summary (for AI chat) ──

def get_family_data_summary(family_id: str) -> dict:
    """Build a complete data snapshot for AI to answer questions about."""
    today = _local_today()

    # Lists and items
    lists_data = []
    for lst in get_lists(family_id):
        items = get_list_items(lst["id"])
        unchecked = [dict(i) for i in items if not i["checked"]]
        checked = [dict(i) for i in items if i["checked"]]
        spending = get_list_spending(lst["id"])
        lists_data.append({
            "name": lst["name"], "icon": lst["icon"],
            "unchecked": [{"text": i["text"], "qty": i.get("quantity", 1),
                           "assigned_to": i.get("assigned_to"), "note": i.get("note")} for i in unchecked],
            "checked": [{"text": i["text"], "price": i.get("price")} for i in checked],
            "total_spent": spending,
        })

    # Chores
    chores_data = []
    for c in get_chores_with_status(family_id, today.isoformat()):
        streak = get_chore_streak(c["id"], today.isoformat())
        chores_data.append({
            "title": c["title"], "assigned_to": c.get("assigned_to", "everyone"),
            "recurrence": c.get("recurrence", "none"),
            "done_today": bool(c.get("done_today")),
            "done_by": c.get("done_by"), "streak": streak,
        })

    # Upcoming events
    upcoming = get_upcoming_items(family_id)
    events_data = [{"title": e["title"], "date": e["start_date"], "time": e.get("time"),
                    "type": e["type"], "location": e.get("location")} for e in upcoming]

    # Pantry
    pantry = get_pantry_items(family_id)
    stale = get_stale_pantry_items(family_id)

    # Recent activity
    activity = get_recent_activity(family_id, limit=20)
    activity_data = [{"who": a["member_name"], "action": a["action"],
                      "when": a["created_at"]} for a in activity]

    # Meal plan
    meal_plan = get_latest_meal_plan(family_id)

    return {
        "lists": lists_data,
        "chores": chores_data,
        "upcoming_events": events_data,
        "pantry_items": [r["text"] for r in pantry],
        "stale_pantry": [{"text": s["text"], "added": s["created_at"]} for s in stale],
        "recent_activity": activity_data,
        "has_meal_plan": meal_plan is not None,
        "today": today.isoformat(),
    }


# ── Activity Log ──

def log_activity(family_id: str, member_name: str, action: str, action_type: str) -> None:
    from uuid import uuid4
    _execute(
        "INSERT INTO activity_log (id, family_id, member_name, action, action_type, created_at) VALUES (?,?,?,?,?,?)",
        (str(uuid4()), family_id, member_name, action, action_type,
         datetime.now(timezone.utc).isoformat()),
    )


def get_recent_activity(family_id: str, limit: int = 15) -> list:
    rows = _execute(
        "SELECT * FROM activity_log WHERE family_id = ? ORDER BY created_at DESC LIMIT ?",
        (family_id, limit), fetch='all')
    return rows or []


# ── Push Subscriptions ──

def save_push_subscription(endpoint: str, device_id: str, member_id: str,
                           family_id: str, p256dh: str, auth: str,
                           user_agent: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat()
    if USE_POSTGRES:
        _execute(
            """INSERT INTO push_subscriptions
               (endpoint, device_id, member_id, family_id, p256dh, auth, user_agent, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (endpoint) DO UPDATE SET
                   device_id = EXCLUDED.device_id,
                   member_id = EXCLUDED.member_id,
                   family_id = EXCLUDED.family_id,
                   p256dh = EXCLUDED.p256dh,
                   auth = EXCLUDED.auth,
                   user_agent = EXCLUDED.user_agent""",
            (endpoint, device_id, member_id, family_id, p256dh, auth, user_agent, now),
        )
    else:
        _execute(
            """INSERT OR REPLACE INTO push_subscriptions
               (endpoint, device_id, member_id, family_id, p256dh, auth, user_agent, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (endpoint, device_id, member_id, family_id, p256dh, auth, user_agent, now),
        )


def delete_push_subscription(endpoint: str) -> None:
    _execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))


def get_push_subscriptions_for_member(member_id: str) -> list:
    return _execute(
        "SELECT * FROM push_subscriptions WHERE member_id = ?",
        (member_id,), fetch='all') or []


def get_push_subscriptions_for_family(family_id: str) -> list:
    return _execute(
        "SELECT * FROM push_subscriptions WHERE family_id = ?",
        (family_id,), fetch='all') or []


def mark_push_success(endpoint: str) -> None:
    _execute(
        "UPDATE push_subscriptions SET last_success_at = ?, failure_count = 0 WHERE endpoint = ?",
        (datetime.now(timezone.utc).isoformat(), endpoint),
    )


# ── Pattern Detection (agentic suggestions) ──

def get_pattern_suggestions(family_id: str) -> list:
    """Detect patterns in family behavior and return smart suggestions.

    Looks for:
    - List items added on the same weekday 2+ times in past 8 weeks
    - Items in pantry that haven't been refilled to grocery list
    """
    suggestions = []
    today = _local_today()
    weekday_name = today.strftime("%A")
    cutoff = (today - timedelta(weeks=8)).isoformat()

    # 1. Recurring list items by weekday
    rows = _execute(
        """SELECT li.text, li.list_id, l.name as list_name, li.created_at
           FROM list_items li
           JOIN lists l ON l.id = li.list_id
           WHERE l.family_id = ? AND li.created_at >= ?
           ORDER BY li.created_at DESC""",
        (family_id, cutoff), fetch='all') or []

    # Group items by (text + list_id) and check if added on same weekday
    item_history = {}
    for r in rows:
        try:
            d = datetime.fromisoformat(r["created_at"].replace('Z', '+00:00')).date()
            key = (r["text"].lower().strip(), r["list_id"])
            if key not in item_history:
                item_history[key] = {"text": r["text"], "list_name": r["list_name"], "list_id": r["list_id"], "days": []}
            item_history[key]["days"].append(d.strftime("%A"))
        except Exception:
            continue

    # Get current unchecked items in each list (so we don't suggest things already there)
    current_items = set()
    for lst in get_lists(family_id):
        for item in get_list_items(lst["id"]):
            if not item.get("checked"):
                current_items.add((item["text"].lower().strip(), lst["id"]))

    # Find items added 2+ times on this weekday
    for key, info in item_history.items():
        same_day_count = info["days"].count(weekday_name)
        if same_day_count >= 2 and key not in current_items:
            suggestions.append({
                "type": "recurring_item",
                "text": info["text"],
                "list_id": info["list_id"],
                "list_name": info["list_name"],
                "reason": f"You usually add this on {weekday_name}s",
                "count": same_day_count,
            })

    # Sort by frequency
    suggestions.sort(key=lambda s: s.get("count", 0), reverse=True)
    return suggestions[:3]  # max 3 suggestions


# Initialise on import
init_db()
