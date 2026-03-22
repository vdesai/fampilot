# Changelog

## v2.0 - Refined Workflow (Current)

### Major Changes

#### Workflow Improvements
- **Deferred Authentication**: Google Calendar authentication now only happens AFTER user confirms the event
- **Show First, Authenticate Later**: Users see extracted event details immediately
- **Cleaner Flow**: Linear workflow - extract → show → confirm → authenticate → create

#### Multi-Day Event Support
- Added `start_date` and `end_date` fields (replacing single `date` field)
- Handles date ranges like "Jul 20-22, 2024"
- Single-day events have `end_date: null`
- Multi-day events show "Dates: YYYY-MM-DD to YYYY-MM-DD"

#### Code Structure Improvements
- Organized into 6 clear functional sections:
  - OCR Extraction
  - AI Event Extraction
  - User Interaction & Confirmation
  - Date/Time Parsing
  - Google Calendar Integration
  - Main Workflow
- Clear function names and documentation
- Better separation of concerns
- Improved error handling

#### Terminal Output
- Minimal, clean output
- Uses ✓ and ✗ symbols for status
- Removed unnecessary verbose messages
- Better visual hierarchy with separators

#### Field Handling
- Location remains `null` if not found (no unnecessary prompts)
- Original time string preserved in event data
- Parsed time used for calendar creation
- Multi-day information added to calendar description

### Technical Changes

#### Function Renames
- `extract_event_info()` → `extract_event_details()` (clearer)
- `add_to_google_calendar()` → `create_calendar_event()` (more accurate)
- `confirm_event_with_user()` now only handles UI, no calendar logic

#### New Fields
```json
{
  "title": "Event Name",
  "start_date": "2024-07-20",
  "end_date": "2024-07-22",  // or null for single-day
  "time": "10AM-4PM",        // original format preserved
  "location": "Venue"        // or null
}
```

#### Removed
- Premature Google Calendar authentication
- Verbose status messages
- Unnecessary emojis in output
- Redundant error messages

### Documentation Updates

#### README.md
- Documented new workflow clearly
- Added multi-day event handling section
- Updated example session output
- Clarified authentication timing
- Added workflow diagram
- Improved troubleshooting section

#### Code Comments
- Added section headers with visual separators
- Improved function docstrings
- Clearer inline comments
- Better organization

---

## v1.0 - Initial Release

### Features
- OCR text extraction from images
- AI-powered event detail extraction via Claude
- Interactive confirmation workflow
- Field editing capability
- Google Calendar integration
- JSON output
- Time parsing for various formats

### Components
- Tesseract OCR integration
- Anthropic Claude API integration
- Google Calendar API integration
- Interactive terminal UI
- Error handling
