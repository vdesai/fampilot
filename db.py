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
        id         TEXT PRIMARY KEY,
        family_id  TEXT NOT NULL REFERENCES families(id),
        name       TEXT NOT NULL,
        icon       TEXT DEFAULT '🛒',
        created_at TEXT NOT NULL,
        archived   INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS list_items (
        id         TEXT PRIMARY KEY,
        list_id    TEXT NOT NULL REFERENCES lists(id),
        text       TEXT NOT NULL,
        checked    INTEGER DEFAULT 0,
        added_by   TEXT,
        created_at TEXT NOT NULL,
        sort_order INTEGER DEFAULT 0
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
]


def init_db() -> None:
    _execute_many(_SCHEMA)


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


def delete_list(list_id: str) -> None:
    _execute("DELETE FROM list_items WHERE list_id=?", (list_id,))
    _execute("DELETE FROM lists WHERE id=?", (list_id,))


def add_list_item(item_id: str, list_id: str, text: str, added_by: str = "") -> None:
    row = _execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM list_items WHERE list_id=?",
        (list_id,), fetch='one')
    sort_order = row["next_order"] if row else 0
    _execute(
        "INSERT INTO list_items (id, list_id, text, added_by, created_at, sort_order) VALUES (?,?,?,?,?,?)",
        (item_id, list_id, text, added_by, datetime.now(timezone.utc).isoformat(), sort_order))


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


# Initialise on import
init_db()
