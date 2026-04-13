#!/usr/bin/env python3
"""
FamPilot MCP Server

Exposes family data as tools that any MCP-compatible AI client can use.
Run with: python mcp_server.py
Or configure in Claude Desktop / Cursor as an MCP server.

Tools:
  - get_lists, get_list_items, add_list_item, check_item
  - get_pantry, add_to_pantry
  - get_chores, mark_chore_done
  - get_upcoming_events
  - get_morning_briefing, get_weekly_recap
  - ask_family_question (AI-powered Q&A over family data)
  - suggest_meals (AI meal suggestions from pantry)
"""

import json
import os
import sys
from uuid import uuid4
from datetime import date

# Ensure we can import from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
import db

# ── Helpers ──

def _get_family_id() -> str:
    """Get the family ID. For single-family setups, returns the first family."""
    fid = os.getenv("FAMPILOT_FAMILY_ID", "")
    if fid:
        return fid
    families = db.get_all_family_ids()
    if not families:
        raise ValueError("No families found. Create one in the web app first.")
    return families[0]


def _today() -> str:
    return date.today().isoformat()


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    return dict(row)


def _rows_to_list(rows) -> list:
    return [_row_to_dict(r) for r in (rows or [])]


# ── MCP Server ──

mcp = FastMCP(
    "FamPilot",
    instructions="Family organizer — lists, pantry, chores, meals, events. "
                 "Manage your family's groceries, track what's in the pantry, "
                 "assign chores, plan meals, and see upcoming events.",
)


# ── Lists ──

@mcp.tool()
def get_lists() -> str:
    """Get all shopping/grocery lists for the family. Returns list names, item counts, and IDs."""
    family_id = _get_family_id()
    lists = _rows_to_list(db.get_list_summary(family_id))
    result = []
    for lst in lists:
        result.append({
            "id": lst["id"],
            "name": lst["name"],
            "icon": lst["icon"],
            "total_items": lst["total_items"],
            "checked_items": lst["checked_items"],
            "remaining": lst["total_items"] - lst["checked_items"],
        })
    return json.dumps(result, indent=2)


@mcp.tool()
def get_list_items(list_name: str) -> str:
    """Get all items from a specific list by name (e.g., 'Groceries', 'Pantry').
    Shows item text, quantity, checked status, assigned person, notes, and price."""
    family_id = _get_family_id()
    lists = db.get_lists(family_id)
    target = None
    for lst in lists:
        if lst["name"].lower() == list_name.lower():
            target = lst
            break
    if not target:
        # Fuzzy match
        for lst in lists:
            if list_name.lower() in lst["name"].lower():
                target = lst
                break
    if not target:
        return json.dumps({"error": f"List '{list_name}' not found"})

    items = _rows_to_list(db.get_list_items(target["id"]))
    result = []
    for item in items:
        result.append({
            "id": item["id"],
            "text": item["text"],
            "quantity": item.get("quantity", 1),
            "checked": bool(item.get("checked")),
            "assigned_to": item.get("assigned_to"),
            "note": item.get("note"),
            "price": item.get("price"),
        })

    spending = db.get_list_spending(target["id"])
    return json.dumps({
        "list": target["name"],
        "items": result,
        "total_spending": spending,
    }, indent=2)


@mcp.tool()
def add_list_item(list_name: str, item_text: str, quantity: int = 1) -> str:
    """Add an item to a list (e.g., add 'Milk' to 'Groceries'). Creates the list if it doesn't exist."""
    family_id = _get_family_id()
    lists = db.get_lists(family_id)
    target = None
    for lst in lists:
        if lst["name"].lower() == list_name.lower():
            target = lst
            break
    if not target:
        list_id = str(uuid4())
        db.create_list(list_id, family_id, list_name)
        target = {"id": list_id, "name": list_name}

    item_id = str(uuid4())
    db.add_list_item(item_id, target["id"], item_text, added_by="MCP", quantity=quantity)
    return json.dumps({"ok": True, "added": item_text, "to": target["name"], "quantity": quantity})


@mcp.tool()
def check_item(list_name: str, item_text: str) -> str:
    """Check off (mark as done) an item from a list by name."""
    family_id = _get_family_id()
    for lst in db.get_lists(family_id):
        if lst["name"].lower() == list_name.lower() or list_name.lower() in lst["name"].lower():
            for item in db.get_list_items(lst["id"]):
                if item["text"].lower() == item_text.lower() or item_text.lower() in item["text"].lower():
                    db.check_list_item(item["id"])
                    return json.dumps({"ok": True, "checked": item["text"], "from": lst["name"]})
    return json.dumps({"error": f"Item '{item_text}' not found in '{list_name}'"})


# ── Pantry ──

@mcp.tool()
def get_pantry() -> str:
    """Get all items currently in the pantry/home inventory. Shows what you have at home."""
    family_id = _get_family_id()
    # Find pantry list and get full details
    for lst in db.get_lists(family_id):
        if lst["name"].lower() in ("pantry", "inventory", "home inventory"):
            items = _rows_to_list(db.get_list_items(lst["id"]))
            unchecked = [{"text": i["text"], "quantity": i.get("quantity", 1)}
                         for i in items if not i.get("checked")]
            stale = _rows_to_list(db.get_stale_pantry_items(family_id))
            stale_names = [s["text"] for s in stale]
            return json.dumps({
                "items": unchecked,
                "count": len(unchecked),
                "running_low": stale_names,
            }, indent=2)
    return json.dumps({"items": [], "count": 0, "running_low": []})


@mcp.tool()
def add_to_pantry(items: str) -> str:
    """Add items to the pantry. Pass comma-separated items like 'rice, chicken, tomatoes (3)'."""
    family_id = _get_family_id()
    pantry = None
    for lst in db.get_lists(family_id):
        if lst["name"].lower() in ("pantry", "inventory", "home inventory"):
            pantry = lst
            break
    if not pantry:
        pantry_id = str(uuid4())
        db.create_list(pantry_id, family_id, "Pantry", icon="🏠")
        pantry = {"id": pantry_id}

    added = []
    for item_text in items.split(","):
        item_text = item_text.strip()
        if item_text:
            db.add_list_item(str(uuid4()), pantry["id"], item_text, added_by="MCP")
            added.append(item_text)
    return json.dumps({"ok": True, "added": added, "count": len(added)})


# ── Chores ──

@mcp.tool()
def get_chores() -> str:
    """Get all family chores and their status for today. Shows who's assigned, if done, and streaks."""
    family_id = _get_family_id()
    chores = _rows_to_list(db.get_chores_with_status(family_id, _today()))
    result = []
    for c in chores:
        streak = db.get_chore_streak(c["id"], _today())
        result.append({
            "title": c["title"],
            "assigned_to": c.get("assigned_to") or "everyone",
            "done_today": bool(c.get("done_today")),
            "done_by": c.get("done_by"),
            "streak": streak,
            "recurrence": c.get("recurrence", "none"),
        })
    return json.dumps(result, indent=2)


@mcp.tool()
def mark_chore_done(chore_title: str, done_by: str = "MCP") -> str:
    """Mark a chore as done for today."""
    family_id = _get_family_id()
    for c in db.get_chores(family_id):
        if c["title"].lower() == chore_title.lower() or chore_title.lower() in c["title"].lower():
            from uuid import uuid4
            db.log_chore_done(str(uuid4()), c["id"], done_by, _today())
            return json.dumps({"ok": True, "chore": c["title"], "done_by": done_by})
    return json.dumps({"error": f"Chore '{chore_title}' not found"})


# ── Events & Calendar ──

@mcp.tool()
def get_upcoming_events() -> str:
    """Get upcoming events, tasks, and reminders for the next 3 days."""
    family_id = _get_family_id()
    items = _rows_to_list(db.get_upcoming_items(family_id))
    result = []
    for item in items:
        result.append({
            "title": item.get("title"),
            "type": item.get("type"),
            "date": item.get("start_date"),
            "time": item.get("time"),
            "location": item.get("location"),
        })
    return json.dumps(result, indent=2)


# ── Briefings ──

@mcp.tool()
def get_morning_briefing() -> str:
    """Get today's morning briefing summary — events, chores, pantry alerts, list items."""
    family_id = _get_family_id()
    return json.dumps(db.build_morning_briefing(family_id), indent=2)


@mcp.tool()
def get_weekly_recap() -> str:
    """Get the weekly family recap — spending, chores completed, streaks, items tracked."""
    family_id = _get_family_id()
    return json.dumps(db.build_weekly_recap(family_id), indent=2)


# ── AI-Powered ──

@mcp.tool()
def ask_about_family(question: str) -> str:
    """Ask any question about the family's data — spending, chores, pantry, events, activity.
    Examples: 'How much did we spend on groceries?', 'Who has the longest chore streak?',
    'What are we running low on?', 'What happened this week?'"""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "ANTHROPIC_API_KEY not set. Cannot use AI features."
    family_id = _get_family_id()
    family_data = db.get_family_data_summary(family_id)
    from main import answer_family_question
    return answer_family_question(question, family_data, api_key)


@mcp.tool()
def suggest_meals(preferences: str = "") -> str:
    """Suggest meals based on what's currently in the pantry.
    Optionally pass preferences like 'vegetarian', 'quick', 'kid-friendly'."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "ANTHROPIC_API_KEY not set. Cannot use AI features."
    family_id = _get_family_id()
    pantry_items = [r["text"] for r in db.get_pantry_items(family_id)]
    from main import suggest_meals_from_pantry
    result = suggest_meals_from_pantry(pantry_items, preferences, api_key)
    return json.dumps(result, indent=2)


# ── Resources ──

@mcp.resource("fampilot://briefing")
def briefing_resource() -> str:
    """Today's family briefing."""
    family_id = _get_family_id()
    return json.dumps(db.build_morning_briefing(family_id))


@mcp.resource("fampilot://pantry")
def pantry_resource() -> str:
    """Current pantry inventory."""
    family_id = _get_family_id()
    items = [r["text"] for r in db.get_pantry_items(family_id)]
    return json.dumps({"items": items, "count": len(items)})


@mcp.resource("fampilot://family-summary")
def family_summary_resource() -> str:
    """Complete family data summary."""
    family_id = _get_family_id()
    return json.dumps(db.get_family_data_summary(family_id), default=str)


# ── Entry point ──

if __name__ == "__main__":
    mcp.run()
