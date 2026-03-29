"""
FamPilot — SQLite persistence layer.

DB_PATH is read from the DB_PATH environment variable.
Default: fampilot.db (relative to working directory).

On Render free tier the filesystem is ephemeral; set DB_PATH to a path
on a Render Persistent Disk (e.g. /data/fampilot.db) to survive redeploys.
"""

import os
import secrets
import string
import sqlite3
from datetime import datetime, date, timedelta, timezone
from typing import Optional


def _local_today() -> date:
    """Return today's date in the configured timezone (APP_TIMEZONE env var)."""
    tz_name = os.getenv("APP_TIMEZONE")
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz_name)).date()
        except Exception:
            pass
    return date.today()


DB_PATH = os.getenv("DB_PATH", "fampilot.db")

_CREATE_SETTINGS = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS items (
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
    reminder_sent       INTEGER DEFAULT 0
)
"""

_CREATE_FAMILIES = """
CREATE TABLE IF NOT EXISTS families (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

_CREATE_MEMBERS = """
CREATE TABLE IF NOT EXISTS members (
    id           TEXT PRIMARY KEY,
    family_id    TEXT NOT NULL REFERENCES families(id),
    display_name TEXT DEFAULT 'Family Member',
    role         TEXT DEFAULT 'member',
    created_at   TEXT NOT NULL
)
"""

_CREATE_DEVICES = """
CREATE TABLE IF NOT EXISTS devices (
    id         TEXT PRIMARY KEY,
    member_id  TEXT NOT NULL REFERENCES members(id),
    last_seen  TEXT,
    user_agent TEXT
)
"""

_CREATE_INVITE_CODES = """
CREATE TABLE IF NOT EXISTS invite_codes (
    code       TEXT PRIMARY KEY,
    family_id  TEXT NOT NULL REFERENCES families(id),
    created_by TEXT REFERENCES members(id),
    expires_at TEXT NOT NULL,
    max_uses   INTEGER DEFAULT 20,
    use_count  INTEGER DEFAULT 0
)
"""

_CREATE_LISTS = """
CREATE TABLE IF NOT EXISTS lists (
    id         TEXT PRIMARY KEY,
    family_id  TEXT NOT NULL REFERENCES families(id),
    name       TEXT NOT NULL,
    icon       TEXT DEFAULT '🛒',
    created_at TEXT NOT NULL,
    archived   INTEGER DEFAULT 0
)
"""

_CREATE_LIST_ITEMS = """
CREATE TABLE IF NOT EXISTS list_items (
    id         TEXT PRIMARY KEY,
    list_id    TEXT NOT NULL REFERENCES lists(id),
    text       TEXT NOT NULL,
    checked    INTEGER DEFAULT 0,
    added_by   TEXT,
    created_at TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0
)
"""

_CREATE_CHORES = """
CREATE TABLE IF NOT EXISTS chores (
    id          TEXT PRIMARY KEY,
    family_id   TEXT NOT NULL REFERENCES families(id),
    title       TEXT NOT NULL,
    icon        TEXT DEFAULT '🧹',
    assigned_to TEXT,
    recurrence  TEXT DEFAULT 'none',
    created_at  TEXT NOT NULL
)
"""

_CREATE_CHORE_LOG = """
CREATE TABLE IF NOT EXISTS chore_log (
    id          TEXT PRIMARY KEY,
    chore_id    TEXT NOT NULL REFERENCES chores(id),
    done_by     TEXT,
    done_date   TEXT NOT NULL,
    created_at  TEXT NOT NULL
)
"""

# Columns added after initial release — ALTER TABLE is idempotent via try/except
_MIGRATIONS = [
    ("reminder_time",        "TEXT"),
    ("reminder_sent",        "INTEGER DEFAULT 0"),
    ("reminder_triggered_at","TEXT"),
    ("group_id",             "TEXT"),
    ("group_title",          "TEXT"),
    ("group_summary",        "TEXT"),
    ("completed",            "INTEGER DEFAULT 0"),
    ("family_id",            "TEXT"),
]


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    """Create tables and run column migrations. Safe to call multiple times."""
    with _conn() as con:
        con.execute(_CREATE_SETTINGS)
        con.execute(_CREATE_TABLE)
        con.execute(_CREATE_FAMILIES)
        con.execute(_CREATE_MEMBERS)
        con.execute(_CREATE_DEVICES)
        con.execute(_CREATE_INVITE_CODES)
        con.execute(_CREATE_LISTS)
        con.execute(_CREATE_LIST_ITEMS)
        con.execute(_CREATE_CHORES)
        con.execute(_CREATE_CHORE_LOG)
        con.execute(_CREATE_MEAL_PLANS)
        for col, defn in _MIGRATIONS:
            try:
                con.execute(f"ALTER TABLE items ADD COLUMN {col} {defn}")
            except sqlite3.OperationalError:
                pass  # column already exists
        con.commit()


# ── Settings ──

def get_setting(key: str) -> Optional[str]:
    with _conn() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with _conn() as con:
        con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
        con.commit()


# ── Families, members, devices ──

def create_family(family_id: str, name: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO families (id, name, created_at) VALUES (?,?,?)",
            (family_id, name, datetime.now(timezone.utc).isoformat()),
        )
        con.commit()


def get_family(family_id: str) -> Optional[sqlite3.Row]:
    with _conn() as con:
        return con.execute("SELECT * FROM families WHERE id=?", (family_id,)).fetchone()


def create_member(member_id: str, family_id: str, display_name: str, role: str = "member") -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO members (id, family_id, display_name, role, created_at) VALUES (?,?,?,?,?)",
            (member_id, family_id, display_name, role, datetime.now(timezone.utc).isoformat()),
        )
        con.commit()


def get_member(member_id: str) -> Optional[sqlite3.Row]:
    with _conn() as con:
        return con.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()


def create_device(device_id: str, member_id: str, user_agent: str = "") -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO devices (id, member_id, last_seen, user_agent) VALUES (?,?,?,?)",
            (device_id, member_id, datetime.now(timezone.utc).isoformat(), user_agent),
        )
        con.commit()


def get_device(device_id: str) -> Optional[sqlite3.Row]:
    with _conn() as con:
        return con.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()


def touch_device(device_id: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE devices SET last_seen=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), device_id),
        )
        con.commit()


def resolve_device(device_id: str) -> Optional[dict]:
    """Given a device_id cookie, return {device, member, family} or None."""
    with _conn() as con:
        row = con.execute(
            """SELECT d.id AS device_id, d.member_id,
                      m.family_id, m.display_name, m.role,
                      f.name AS family_name
               FROM devices d
               JOIN members m ON m.id = d.member_id
               JOIN families f ON f.id = m.family_id
               WHERE d.id = ?""",
            (device_id,),
        ).fetchone()
        if not row:
            return None
        return dict(row)


def get_family_members(family_id: str) -> list:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM members WHERE family_id=? ORDER BY created_at",
            (family_id,),
        ).fetchall()


# ── Invite codes ──

def _generate_code() -> str:
    chars = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(chars) for _ in range(6))


def create_invite_code(family_id: str, created_by: str) -> str:
    code = _generate_code()
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    with _conn() as con:
        con.execute(
            "INSERT INTO invite_codes (code, family_id, created_by, expires_at) VALUES (?,?,?,?)",
            (code, family_id, created_by, expires),
        )
        con.commit()
    return code


def get_invite_code(code: str) -> Optional[sqlite3.Row]:
    with _conn() as con:
        return con.execute("SELECT * FROM invite_codes WHERE code=?", (code.upper(),)).fetchone()


def use_invite_code(code: str) -> bool:
    """Increment use_count. Returns False if expired or maxed out."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        row = con.execute("SELECT * FROM invite_codes WHERE code=?", (code.upper(),)).fetchone()
        if not row:
            return False
        if row["expires_at"] < now:
            return False
        if row["use_count"] >= row["max_uses"]:
            return False
        con.execute(
            "UPDATE invite_codes SET use_count = use_count + 1 WHERE code=?",
            (code.upper(),),
        )
        con.commit()
        return True


def get_active_invite_code(family_id: str) -> Optional[str]:
    """Return the active (non-expired, not maxed) invite code for a family, or None."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        row = con.execute(
            """SELECT code FROM invite_codes
               WHERE family_id=? AND expires_at > ? AND use_count < max_uses
               ORDER BY expires_at DESC LIMIT 1""",
            (family_id, now),
        ).fetchone()
        return row["code"] if row else None


# ── Legacy family token (for /family/:token share page) ──

def get_or_create_family_token() -> str:
    token = get_setting("family_token")
    if not token:
        token = secrets.token_urlsafe(12)
        set_setting("family_token", token)
    return token


# ── Items (now scoped by family_id) ──

def get_family_week(family_id: str) -> list:
    """All non-completed items from today through the next 7 days for a family."""
    today   = _local_today()
    in7days = (today + timedelta(days=7)).isoformat()
    today   = today.isoformat()
    with _conn() as con:
        return con.execute(
            """SELECT * FROM items
               WHERE family_id = ?
                 AND start_date BETWEEN ? AND ?
                 AND (completed IS NULL OR completed = 0)
               ORDER BY start_date, time""",
            (family_id, today, in7days),
        ).fetchall()


def save_item(item_id: str, result: dict,
              source_text: Optional[str] = None,
              image_path: Optional[str] = None,
              family_id: Optional[str] = None) -> None:
    """Upsert a classified result into the DB."""
    rtype = result.get("type")
    data = result.get("data", {})
    start_date = data.get("start_date") or (data.get("due_date") if rtype == "task" else None)

    with _conn() as con:
        con.execute(
            """INSERT OR REPLACE INTO items
               (id, created_at, type, confidence, reasoning,
                title, start_date, end_date, time, location,
                notes, priority, remind_at,
                original_input_text, uploaded_image_path, family_id)
               VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?, ?,?,?)""",
            (
                item_id,
                datetime.now(timezone.utc).isoformat(),
                rtype,
                result.get("confidence"),
                result.get("reasoning"),
                data.get("title"),
                start_date,
                data.get("end_date"),
                data.get("time"),
                data.get("location"),
                data.get("notes"),
                data.get("priority"),
                data.get("remind_at"),
                source_text,
                image_path,
                family_id,
            ),
        )
        con.commit()


def save_flat_item(item_id: str, flat: dict,
                   source_text: Optional[str] = None,
                   image_path: Optional[str] = None,
                   group_id: Optional[str] = None,
                   group_title: Optional[str] = None,
                   group_summary: Optional[str] = None,
                   family_id: Optional[str] = None) -> None:
    """Save a flat item dict (from multi-extraction) directly to DB."""
    with _conn() as con:
        con.execute(
            """INSERT OR REPLACE INTO items
               (id, created_at, type, confidence, reasoning,
                title, start_date, end_date, time, location,
                notes, priority, remind_at,
                original_input_text, uploaded_image_path,
                group_id, group_title, group_summary, family_id)
               VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?, ?,?, ?,?,?,?)""",
            (
                item_id,
                datetime.now(timezone.utc).isoformat(),
                flat.get("type"),
                flat.get("confidence"),
                flat.get("reasoning"),
                flat.get("title"),
                flat.get("start_date"),
                flat.get("end_date"),
                flat.get("time"),
                flat.get("location"),
                flat.get("notes"),
                flat.get("priority"),
                flat.get("remind_at"),
                source_text,
                image_path,
                group_id,
                group_title,
                group_summary,
                family_id,
            ),
        )
        con.commit()


def update_type(item_id: str, new_type: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE items SET type=?, confidence=1.0, reasoning=? WHERE id=?",
            (new_type, f"Manually set to '{new_type}' by user.", item_id),
        )
        con.commit()


def update_event_data(item_id: str, data: dict) -> None:
    with _conn() as con:
        con.execute(
            """UPDATE items
               SET title=?, start_date=?, end_date=?, time=?, location=?
               WHERE id=?""",
            (
                data.get("title"),
                data.get("start_date"),
                data.get("end_date"),
                data.get("time"),
                data.get("location"),
                item_id,
            ),
        )
        con.commit()


def update_item(item_id: str, rtype: str, fields: dict) -> None:
    start_date = fields.get("start_date") or fields.get("due_date")
    with _conn() as con:
        con.execute(
            """UPDATE items
               SET title=?, start_date=?, end_date=?, time=?,
                   location=?, notes=?, priority=?, remind_at=?,
                   reminder_time=?, reminder_sent=0
               WHERE id=?""",
            (
                fields.get("title"),
                start_date,
                fields.get("end_date"),
                fields.get("time"),
                fields.get("location"),
                fields.get("notes"),
                fields.get("priority"),
                fields.get("remind_at"),
                fields.get("reminder_time") or None,
                item_id,
            ),
        )
        con.commit()


def get_due_reminders() -> list:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        return con.execute(
            """SELECT * FROM items
               WHERE reminder_time IS NOT NULL
                 AND reminder_time <= ?
                 AND (reminder_sent IS NULL OR reminder_sent = 0)""",
            (now,),
        ).fetchall()


def mark_reminder_sent(item_id: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            "UPDATE items SET reminder_sent=1, reminder_triggered_at=? WHERE id=?",
            (now, item_id),
        )
        con.commit()


def get_recent_reminders(family_id: str, hours: int = 6) -> list:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _conn() as con:
        return con.execute(
            """SELECT * FROM items
               WHERE family_id = ?
                 AND reminder_triggered_at IS NOT NULL
                 AND reminder_triggered_at >= ?
                 AND (reminder_sent IS NULL OR reminder_sent != 2)
               ORDER BY reminder_triggered_at DESC""",
            (family_id, cutoff),
        ).fetchall()


def dismiss_reminder(item_id: str) -> None:
    with _conn() as con:
        con.execute("UPDATE items SET reminder_sent=2 WHERE id=?", (item_id,))
        con.commit()


def get_upcoming_items(family_id: str) -> list:
    today    = _local_today()
    in3days  = (today + timedelta(days=3)).isoformat()
    today    = today.isoformat()
    with _conn() as con:
        return con.execute(
            """SELECT * FROM items
               WHERE family_id = ?
                 AND start_date BETWEEN ? AND ?
                 AND (completed IS NULL OR completed = 0)
               ORDER BY start_date, time""",
            (family_id, today, in3days),
        ).fetchall()


def complete_item(item_id: str) -> None:
    with _conn() as con:
        con.execute("UPDATE items SET completed=1 WHERE id=?", (item_id,))
        con.commit()


def uncomplete_item(item_id: str) -> None:
    with _conn() as con:
        con.execute("UPDATE items SET completed=0 WHERE id=?", (item_id,))
        con.commit()


def delete_item(item_id: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM items WHERE id=?", (item_id,))
        con.commit()


def get_history(family_id: str, limit: int = 100) -> list:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM items WHERE family_id=? ORDER BY created_at DESC LIMIT ?",
            (family_id, limit),
        ).fetchall()


def get_item(item_id: str) -> Optional[sqlite3.Row]:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM items WHERE id=?", (item_id,)
        ).fetchone()


def row_to_result(row: sqlite3.Row) -> dict:
    rtype = row["type"]
    if rtype == "event":
        data = {
            "title":      row["title"],
            "start_date": row["start_date"],
            "end_date":   row["end_date"],
            "time":       row["time"],
            "location":   row["location"],
        }
    elif rtype == "task":
        data = {
            "title":    row["title"],
            "due_date": row["start_date"],
            "priority": row["priority"],
            "notes":    row["notes"],
        }
    else:
        data = {
            "title":     row["title"],
            "remind_at": row["remind_at"],
            "notes":     row["notes"],
        }
    return {
        "type":       rtype,
        "confidence": row["confidence"] if row["confidence"] is not None else 1.0,
        "reasoning":  row["reasoning"] or "",
        "data":       data,
    }


# ── Shopping Lists ──

def create_list(list_id: str, family_id: str, name: str, icon: str = "🛒") -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO lists (id, family_id, name, icon, created_at) VALUES (?,?,?,?,?)",
            (list_id, family_id, name, icon, datetime.now(timezone.utc).isoformat()),
        )
        con.commit()


def get_lists(family_id: str) -> list:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM lists WHERE family_id=? AND (archived=0 OR archived IS NULL) ORDER BY created_at DESC",
            (family_id,),
        ).fetchall()


def get_list(list_id: str) -> Optional[sqlite3.Row]:
    with _conn() as con:
        return con.execute("SELECT * FROM lists WHERE id=?", (list_id,)).fetchone()


def rename_list(list_id: str, name: str) -> None:
    with _conn() as con:
        con.execute("UPDATE lists SET name=? WHERE id=?", (name, list_id))
        con.commit()


def archive_list(list_id: str) -> None:
    with _conn() as con:
        con.execute("UPDATE lists SET archived=1 WHERE id=?", (list_id,))
        con.commit()


def delete_list(list_id: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM list_items WHERE list_id=?", (list_id,))
        con.execute("DELETE FROM lists WHERE id=?", (list_id,))
        con.commit()


def add_list_item(item_id: str, list_id: str, text: str, added_by: str = "") -> None:
    with _conn() as con:
        # Get max sort_order for this list
        row = con.execute(
            "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM list_items WHERE list_id=?",
            (list_id,),
        ).fetchone()
        sort_order = row["next_order"] if row else 0
        con.execute(
            "INSERT INTO list_items (id, list_id, text, added_by, created_at, sort_order) VALUES (?,?,?,?,?,?)",
            (item_id, list_id, text, added_by, datetime.now(timezone.utc).isoformat(), sort_order),
        )
        con.commit()


def get_list_items(list_id: str) -> list:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM list_items WHERE list_id=? ORDER BY checked ASC, sort_order ASC",
            (list_id,),
        ).fetchall()


def check_list_item(item_id: str) -> None:
    with _conn() as con:
        con.execute("UPDATE list_items SET checked=1 WHERE id=?", (item_id,))
        con.commit()


def uncheck_list_item(item_id: str) -> None:
    with _conn() as con:
        con.execute("UPDATE list_items SET checked=0 WHERE id=?", (item_id,))
        con.commit()


def delete_list_item(item_id: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM list_items WHERE id=?", (item_id,))
        con.commit()


def clear_checked_items(list_id: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM list_items WHERE list_id=? AND checked=1", (list_id,))
        con.commit()


def get_list_summary(family_id: str) -> list:
    """Return lists with item counts for the family."""
    with _conn() as con:
        return con.execute(
            """SELECT l.*,
                      COUNT(li.id) AS total_items,
                      SUM(CASE WHEN li.checked = 1 THEN 1 ELSE 0 END) AS checked_items
               FROM lists l
               LEFT JOIN list_items li ON li.list_id = l.id
               WHERE l.family_id = ? AND (l.archived = 0 OR l.archived IS NULL)
               GROUP BY l.id
               ORDER BY l.created_at DESC""",
            (family_id,),
        ).fetchall()


# ── Chores ──

def create_chore(chore_id: str, family_id: str, title: str,
                 icon: str = "🧹", assigned_to: str = "",
                 recurrence: str = "none") -> None:
    with _conn() as con:
        con.execute(
            """INSERT INTO chores (id, family_id, title, icon, assigned_to, recurrence, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (chore_id, family_id, title, icon, assigned_to, recurrence,
             datetime.now(timezone.utc).isoformat()),
        )
        con.commit()


def get_chores(family_id: str) -> list:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM chores WHERE family_id=? ORDER BY created_at",
            (family_id,),
        ).fetchall()


def get_chore(chore_id: str) -> Optional[sqlite3.Row]:
    with _conn() as con:
        return con.execute("SELECT * FROM chores WHERE id=?", (chore_id,)).fetchone()


def update_chore(chore_id: str, title: str, assigned_to: str,
                 recurrence: str, icon: str) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE chores SET title=?, assigned_to=?, recurrence=?, icon=? WHERE id=?",
            (title, assigned_to, recurrence, icon, chore_id),
        )
        con.commit()


def delete_chore(chore_id: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM chore_log WHERE chore_id=?", (chore_id,))
        con.execute("DELETE FROM chores WHERE id=?", (chore_id,))
        con.commit()


def log_chore_done(log_id: str, chore_id: str, done_by: str, done_date: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO chore_log (id, chore_id, done_by, done_date, created_at) VALUES (?,?,?,?,?)",
            (log_id, chore_id, done_by, done_date, datetime.now(timezone.utc).isoformat()),
        )
        con.commit()


def undo_chore_done(chore_id: str, done_date: str) -> None:
    with _conn() as con:
        con.execute(
            "DELETE FROM chore_log WHERE chore_id=? AND done_date=?",
            (chore_id, done_date),
        )
        con.commit()


def get_chore_log_for_date(family_id: str, target_date: str) -> list:
    """Return all chore log entries for a given date for this family."""
    with _conn() as con:
        return con.execute(
            """SELECT cl.*, c.title, c.icon, c.assigned_to
               FROM chore_log cl
               JOIN chores c ON c.id = cl.chore_id
               WHERE c.family_id = ? AND cl.done_date = ?""",
            (family_id, target_date),
        ).fetchall()


def get_chores_with_status(family_id: str, target_date: str) -> list:
    """Return all chores for the family, with done status for the given date."""
    with _conn() as con:
        return con.execute(
            """SELECT c.*,
                      CASE WHEN cl.id IS NOT NULL THEN 1 ELSE 0 END AS done_today,
                      cl.done_by
               FROM chores c
               LEFT JOIN chore_log cl
                    ON cl.chore_id = c.id AND cl.done_date = ?
               WHERE c.family_id = ?
               ORDER BY c.assigned_to, c.title""",
            (target_date, family_id),
        ).fetchall()


def get_chore_streak(chore_id: str, today: str) -> int:
    """Count consecutive days (up to today) that a chore was done."""
    with _conn() as con:
        rows = con.execute(
            "SELECT DISTINCT done_date FROM chore_log WHERE chore_id=? ORDER BY done_date DESC",
            (chore_id,),
        ).fetchall()
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

_CREATE_MEAL_PLANS = """
CREATE TABLE IF NOT EXISTS meal_plans (
    id          TEXT PRIMARY KEY,
    family_id   TEXT NOT NULL REFERENCES families(id),
    created_at  TEXT NOT NULL,
    days        INTEGER DEFAULT 7,
    preferences TEXT,
    meals_json  TEXT NOT NULL
)
"""


# ── Usage tracking (freemium) ──

def save_meal_plan(plan_id: str, family_id: str, meals_json: str,
                   days: int = 7, preferences: str = "") -> None:
    with _conn() as con:
        con.execute(
            """INSERT INTO meal_plans (id, family_id, created_at, days, preferences, meals_json)
               VALUES (?,?,?,?,?,?)""",
            (plan_id, family_id, datetime.now(timezone.utc).isoformat(),
             days, preferences, meals_json),
        )
        con.commit()


def get_latest_meal_plan(family_id: str) -> Optional[sqlite3.Row]:
    with _conn() as con:
        return con.execute(
            "SELECT * FROM meal_plans WHERE family_id=? ORDER BY created_at DESC LIMIT 1",
            (family_id,),
        ).fetchone()


def get_meal_plan(plan_id: str) -> Optional[sqlite3.Row]:
    with _conn() as con:
        return con.execute("SELECT * FROM meal_plans WHERE id=?", (plan_id,)).fetchone()


def delete_meal_plan(plan_id: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM meal_plans WHERE id=?", (plan_id,))
        con.commit()


FREE_SCAN_LIMIT = 5  # per family per month

def get_scan_count(family_id: str) -> int:
    """Count AI scans this month for a family."""
    month_start = _local_today().replace(day=1).isoformat()
    with _conn() as con:
        row = con.execute(
            """SELECT COUNT(*) AS cnt FROM items
               WHERE family_id = ? AND created_at >= ?""",
            (family_id, month_start),
        ).fetchone()
        return row["cnt"] if row else 0


def can_scan(family_id: str) -> bool:
    """Check if the family has remaining free scans this month."""
    # Check if family has premium
    premium = get_setting(f"premium:{family_id}")
    if premium == "1":
        return True
    return get_scan_count(family_id) < FREE_SCAN_LIMIT


def get_usage_info(family_id: str) -> dict:
    """Return usage info for display."""
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
