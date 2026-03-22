# Google Calendar URL Integration

## Overview

Added a "Add to Google Calendar" button that uses Google Calendar's URL format to create pre-filled calendar events **without requiring authentication**.

This is a fallback method when Google API is not configured.

---

## How It Works

### URL Format

Google Calendar supports creating events via URL with pre-filled details:

```
https://calendar.google.com/calendar/render?action=TEMPLATE&text=EVENT_TITLE&dates=START/END&details=DESCRIPTION&location=LOCATION
```

### Parameters

| Parameter | Description | Format | Example |
|-----------|-------------|--------|---------|
| `action` | Action type | `TEMPLATE` | `TEMPLATE` |
| `text` | Event title | URL encoded | `Summer%20Festival` |
| `dates` | Start/End times | `YYYYMMDDTHHMMSS/YYYYMMDDTHHMMSS` | `20240720T100000/20240720T160000` |
| `details` | Description | URL encoded | `Event%20extracted%20by%20FamPilot` |
| `location` | Venue/Address | URL encoded | `Central%20Park` |

---

## Implementation

### 1. Time Parsing (`parse_time_simple`)

Parses various time formats:

```python
def parse_time_simple(time_str: str, date_str: str) -> tuple:
    """
    Parses: "10AM-4PM", "6:00 PM - 11:00 PM", etc.
    Returns: (start_datetime, end_datetime)
    """
```

**Supported Formats:**
- `10AM-4PM` → 10:00 AM to 4:00 PM
- `6:00 PM - 11:00 PM` → 6:00 PM to 11:00 PM
- `9:30AM-5:30PM` → 9:30 AM to 5:30 PM

**Fallback:**
- If parsing fails: 9 AM - 5 PM
- If no time provided: 9 AM - 5 PM

### 2. URL Generation (`generate_google_calendar_url`)

Creates the Google Calendar URL:

```python
def generate_google_calendar_url(event: Dict) -> str:
    """
    Generates pre-filled Google Calendar URL.
    No authentication required.
    """
```

**Process:**
1. Extract event details (title, date, time, location)
2. Parse date and time strings
3. Format dates as `YYYYMMDDTHHMMSS`
4. URL encode all parameters
5. Build complete URL

**Example Output:**
```
https://calendar.google.com/calendar/render?
  action=TEMPLATE&
  text=Summer%20Music%20Festival&
  dates=20240720T180000/20240720T230000&
  details=Event%20extracted%20by%20FamPilot%0ATime%3A%206%3A00%20PM%20-%2011%3A00%20PM&
  location=Central%20Park%20Amphitheater
```

### 3. Template Integration

Added to `result.html`:

```html
<!-- Quick Add to Calendar (No Auth Required) -->
{% if calendar_url %}
<div class="quick-add-section">
    <div class="quick-add-title">Quick Add (No Login)</div>
    <div class="quick-add-description">
        Or add to Google Calendar directly without authentication
    </div>
    <a href="{{ calendar_url }}" target="_blank" class="button btn-calendar-quick">
        <span>📅</span>
        <span>Add to Google Calendar</span>
    </a>
</div>
{% endif %}
```

---

## User Flow

### Result Page Display

```
┌─────────────────────────────────────────┐
│          [Event Details Card]           │
│                                         │
│  📌 Title: Summer Festival             │
│  📅 Dates: 2024-07-20 to 2024-07-22    │
│  🕐 Time: 6:00 PM - 11:00 PM           │
│  📍 Location: Central Park             │
└─────────────────────────────────────────┘

┌──────────┐ ┌──────────┐ ┌──────────┐
│ ✓ Confirm│ │ ✏️ Edit   │ │ ✗ Cancel │
└──────────┘ └──────────┘ └──────────┘

        ─────────────────────
        Quick Add (No Login)

        Or add to Google Calendar directly
        without authentication

        ┌─────────────────────────────┐
        │ 📅 Add to Google Calendar  │
        └─────────────────────────────┘
```

### User Actions

**Option 1: Confirm Button (API Method)**
- Authenticates with Google API
- Creates event via API
- Full control and integration
- Requires credentials.json

**Option 2: Quick Add Button (URL Method)**
- Opens Google Calendar in new tab
- Pre-filled event form
- User reviews and clicks "Save"
- **No authentication required**

---

## Comparison: API vs URL Method

| Feature | API Method | URL Method |
|---------|-----------|------------|
| **Authentication** | Required (OAuth2) | Not required |
| **Setup** | credentials.json needed | None |
| **User Action** | None (auto-added) | Click "Save" in Google |
| **Control** | Full programmatic | User confirms |
| **Event Creation** | Automatic | Manual (pre-filled) |
| **Use Case** | Frequent use | Quick/one-time |

---

## When to Use Each Method

### URL Method (Quick Add)
✅ No Google API credentials
✅ Quick one-time events
✅ Want user to review before adding
✅ Don't want to set up OAuth
✅ Testing/demo purposes

### API Method (Confirm Button)
✅ Have credentials.json configured
✅ Automated workflow
✅ Frequent event creation
✅ No user interaction needed
✅ Production environment

---

## Multi-Day Events

For multi-day events, the URL method:

1. Uses the **start date** for the calendar entry
2. Adds the **date range** to the description
3. User can adjust in Google Calendar interface

**Example:**
```
Event: "Summer Festival"
Dates: 2024-07-20 to 2024-07-22
Time: 6:00 PM - 11:00 PM

Generated URL creates:
- Start: 2024-07-20 6:00 PM
- End: 2024-07-20 11:00 PM
- Description: "Multi-day event: 2024-07-20 to 2024-07-22"
```

User can then extend the event in Google Calendar UI.

---

## Error Handling

### Missing Date
```python
if not start_date:
    return ""  # Empty URL, button won't show
```

### Invalid Time Format
```python
try:
    # Parse time
except:
    # Fallback to 9 AM - 5 PM
```

### URL Encoding
```python
from urllib.parse import quote

title = quote("Summer Festival!")
# Output: "Summer%20Festival%21"
```

---

## Testing

### Test URL Generation

```python
event = {
    "title": "Summer Music Festival",
    "start_date": "2024-07-20",
    "end_date": "2024-07-22",
    "time": "6:00 PM - 11:00 PM",
    "location": "Central Park"
}

url = generate_google_calendar_url(event)
print(url)
```

### Test in Browser

1. Start the web server
2. Upload an event image
3. See the "Quick Add" button
4. Click to open Google Calendar
5. Review pre-filled details
6. Click "Save" in Google Calendar

---

## Advantages

✅ **No Authentication** - Works without credentials.json
✅ **User Review** - User sees details before saving
✅ **Instant Setup** - No configuration needed
✅ **Fallback Option** - Always available
✅ **Transparent** - User controls final save
✅ **Browser-Based** - Works in any browser
✅ **No Token Management** - No OAuth flow

---

## Limitations

⚠️ **Manual Save** - User must click "Save" in Google
⚠️ **Single Day** - Multi-day events need manual adjustment
⚠️ **Browser Required** - Opens new tab
⚠️ **No Automation** - Can't be fully automated
⚠️ **URL Length** - Very long descriptions may truncate

---

## Code Changes

### Files Modified

**app.py:**
- Added `parse_time_simple()` function
- Added `generate_google_calendar_url()` function
- Updated `/upload` endpoint to generate URL
- Updated `/edit/{event_id}` endpoint to regenerate URL

**templates/result.html:**
- Added "Quick Add" section
- Added styling for calendar button
- Added conditional display based on URL availability

---

## Future Enhancements

Possible improvements:

- [ ] Better multi-day event handling
- [ ] Add recurrence support
- [ ] Add reminders/notifications
- [ ] Support other calendar providers (Outlook, Apple)
- [ ] Generate .ics file download
- [ ] QR code for mobile calendar add

---

## Example URLs

### Simple Event
```
https://calendar.google.com/calendar/render?
action=TEMPLATE&
text=Team%20Meeting&
dates=20240315T140000/20240315T150000&
details=Weekly%20team%20sync&
location=Conference%20Room%20A
```

### All-Day Event
```
https://calendar.google.com/calendar/render?
action=TEMPLATE&
text=Company%20Holiday&
dates=20240704/20240705&
details=Independence%20Day
```

### Multi-Day Conference
```
https://calendar.google.com/calendar/render?
action=TEMPLATE&
text=Tech%20Conference%202024&
dates=20240801T090000/20240801T170000&
details=Multi-day%20event%3A%202024-08-01%20to%202024-08-03&
location=Convention%20Center
```

---

Built as a flexible fallback for Google Calendar integration ✨
