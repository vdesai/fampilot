"""
FamPilot — SQLite persistence layer.

DB_PATH is read from the DB_PATH environment variable.
Default: fampilot.db (relative to working directory).

On Render free tier the filesystem is ephemeral; set DB_PATH to a path
on a Render Persistent Disk (e.g. /data/fampilot.db) to survive redeploys.
"""

import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.getenv("DB_PATH", "fampilot.db")

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

# Columns added after initial release — ALTER TABLE is idempotent via try/except
_MIGRATIONS = [
    ("reminder_time",        "TEXT"),
    ("reminder_sent",        "INTEGER DEFAULT 0"),
    ("reminder_triggered_at","TEXT"),
    ("group_id",             "TEXT"),
    ("group_title",          "TEXT"),
    ("group_summary",        "TEXT"),
]


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    """Create tables and run column migrations. Safe to call multiple times."""
    with _conn() as con:
        con.execute(_CREATE_TABLE)
        for col, defn in _MIGRATIONS:
            try:
                con.execute(f"ALTER TABLE items ADD COLUMN {col} {defn}")
            except sqlite3.OperationalError:
                pass  # column already exists
        con.commit()


def save_item(item_id: str, result: dict,
              source_text: Optional[str] = None,
              image_path: Optional[str] = None) -> None:
    """Upsert a classified result into the DB."""
    rtype = result.get("type")
    data = result.get("data", {})

    # Tasks store their due_date in start_date column
    start_date = data.get("start_date") or (data.get("due_date") if rtype == "task" else None)

    with _conn() as con:
        con.execute(
            """INSERT OR REPLACE INTO items
               (id, created_at, type, confidence, reasoning,
                title, start_date, end_date, time, location,
                notes, priority, remind_at,
                original_input_text, uploaded_image_path)
               VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?, ?,?)""",
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
            ),
        )
        con.commit()


def save_flat_item(item_id: str, flat: dict,
                   source_text: Optional[str] = None,
                   image_path: Optional[str] = None,
                   group_id: Optional[str] = None,
                   group_title: Optional[str] = None,
                   group_summary: Optional[str] = None) -> None:
    """Save a flat item dict (from multi-extraction) directly to DB."""
    with _conn() as con:
        con.execute(
            """INSERT OR REPLACE INTO items
               (id, created_at, type, confidence, reasoning,
                title, start_date, end_date, time, location,
                notes, priority, remind_at,
                original_input_text, uploaded_image_path,
                group_id, group_title, group_summary)
               VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?, ?,?, ?,?,?)""",
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
            ),
        )
        con.commit()


def update_type(item_id: str, new_type: str) -> None:
    """Update just the type + clear confidence/reasoning after user override."""
    with _conn() as con:
        con.execute(
            "UPDATE items SET type=?, confidence=1.0, reasoning=? WHERE id=?",
            (new_type, f"Manually set to '{new_type}' by user.", item_id),
        )
        con.commit()


def update_event_data(item_id: str, data: dict) -> None:
    """Update editable event fields after user edits."""
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
    """Persist edited fields for any item type."""
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
    """Items whose reminder_time has passed and haven't been notified yet."""
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


def get_recent_reminders(hours: int = 6) -> list:
    """Items triggered within the last N hours and not yet dismissed."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _conn() as con:
        return con.execute(
            """SELECT * FROM items
               WHERE reminder_triggered_at IS NOT NULL
                 AND reminder_triggered_at >= ?
                 AND (reminder_sent IS NULL OR reminder_sent != 2)
               ORDER BY reminder_triggered_at DESC""",
            (cutoff,),
        ).fetchall()


def dismiss_reminder(item_id: str) -> None:
    """Mark a triggered reminder as dismissed (reminder_sent=2)."""
    with _conn() as con:
        con.execute("UPDATE items SET reminder_sent=2 WHERE id=?", (item_id,))
        con.commit()


def get_upcoming_items() -> list:
    """Items with start_date from today through the next 3 days, ordered by date+time."""
    from datetime import date, timedelta
    today    = date.today().isoformat()
    in3days  = (date.today() + timedelta(days=3)).isoformat()
    with _conn() as con:
        return con.execute(
            """SELECT * FROM items
               WHERE start_date BETWEEN ? AND ?
               ORDER BY start_date, time""",
            (today, in3days),
        ).fetchall()


def delete_item(item_id: str) -> None:
    """Permanently delete an item."""
    with _conn() as con:
        con.execute("DELETE FROM items WHERE id=?", (item_id,))
        con.commit()


def get_history(limit: int = 100) -> list:
    """Return items ordered newest first."""
    with _conn() as con:
        return con.execute(
            "SELECT * FROM items ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()


def get_item(item_id: str) -> Optional[sqlite3.Row]:
    """Fetch a single item by id."""
    with _conn() as con:
        return con.execute(
            "SELECT * FROM items WHERE id=?", (item_id,)
        ).fetchone()


def row_to_result(row: sqlite3.Row) -> dict:
    """Reconstruct the result dict (used by result.html) from a DB row."""
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
            "due_date": row["start_date"],   # stored in start_date column
            "priority": row["priority"],
            "notes":    row["notes"],
        }
    else:  # reminder
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


# Initialise on import — CREATE TABLE IF NOT EXISTS is idempotent
init_db()
