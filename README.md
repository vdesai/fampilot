# FamPilot - Interactive Event Assistant

An AI-powered assistant that extracts event information from images and adds them to Google Calendar.

## Features

- 📸 **Smart OCR** - Uses Tesseract (local) or Claude Vision (production)
- 🧠 **Smart Event Detection** - Claude AI identifies event details automatically
- 📅 **Multi-Day Support** - Handles date ranges like "July 20-22, 2024"
- ✏️ **Easy Editing** - Modify any field before confirming
- 📅 **Google Calendar Integration** - Automatically adds confirmed events to your calendar
- 🎯 **Clean Workflow** - Minimal, focused terminal output
- 🚀 **Easy Deployment** - No system dependencies required for production

## Quick Start

### Option 1: Web Interface (Recommended)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set API key
export ANTHROPIC_API_KEY='your-api-key-here'

# 3. Run web server
python3 app.py

# 4. Open browser
# Navigate to http://localhost:8000
```

**Note:** Works with or without Tesseract! See [OCR Modes](#ocr-modes) below.

See **[WEB_README.md](WEB_README.md)** for detailed web interface documentation.

### Option 2: Command Line

```bash
# 1. (Optional) Install Tesseract OCR for faster processing
brew install tesseract  # macOS
# sudo apt-get install tesseract-ocr  # Linux

# 2. Install Python packages
pip install -r requirements.txt

# 3. Set API key
export ANTHROPIC_API_KEY='your-api-key-here'

# 4. Run
python3 main.py event_image.png
```

**Note:** Tesseract is optional. Without it, the app uses Claude Vision API (slower but works everywhere).

## Workflow

The assistant follows a clean, linear workflow:

```
1. Extract text from image (OCR)
           ↓
2. Analyze with Claude AI
           ↓
3. Show extracted event
           ↓
4. Ask: yes / edit / no
           ↓
5. [Only if "yes"] Authenticate with Google Calendar
           ↓
6. Create calendar event
```

**Key principle**: Google Calendar authentication only happens after user confirms the event.

## Example Session

```bash
$ python3 main.py flyer.png

Analyzing: flyer.png
✓ Extracted 540 characters

Processing with AI...
✓ Event details extracted

==================================================
📋 Event Details
==================================================
Title:    Summer Music Festival
Dates:    2024-07-20 to 2024-07-22
Time:     6:00 PM - 11:00 PM
Location: Central Park Amphitheater
==================================================

Add to calendar? (yes/edit/no): yes

Authenticating with Google Calendar...
✓ Connected to Google Calendar

✓ Event added to Google Calendar
  https://calendar.google.com/calendar/event?eid=...

Final event data:
{
  "title": "Summer Music Festival",
  "start_date": "2024-07-20",
  "end_date": "2024-07-22",
  "time": "6:00 PM - 11:00 PM",
  "location": "Central Park Amphitheater"
}
```

## Multi-Day Events

The assistant intelligently handles date ranges:

### Single-Day Event
```
Input:  "July 20, 2024"
Output: {
  "start_date": "2024-07-20",
  "end_date": null
}
```

### Multi-Day Event
```
Input:  "July 20-22, 2024"
Output: {
  "start_date": "2024-07-20",
  "end_date": "2024-07-22"
}
```

**Note**: For multi-day events, the assistant creates a calendar event on the start date with the full date range in the description.

## Time Parsing

The assistant handles various time formats:

| Input Format | Start Time | End Time |
|-------------|------------|----------|
| `10AM-4PM` | 10:00 AM | 4:00 PM |
| `6:00 PM - 11:00 PM` | 6:00 PM | 11:00 PM |
| `9:30AM-5:30PM` | 9:30 AM | 5:30 PM |
| `2PM` (single time) | 2:00 PM | 3:00 PM (1 hour default) |

## Editing Fields

Choose "edit" at the confirmation prompt to modify any field:

```
Add to calendar? (yes/edit/no): edit

==================================================
Edit Mode
==================================================
1. Title
2. Start Date
3. End Date
4. Time
5. Location
6. Done editing
==================================================

Edit field (1-6): 4

Current time: 10AM-4PM
Enter new time (or press Enter to keep): 11AM-5PM
✓ Updated time

Edit field (1-6): 6

✓ Finished editing
```

## Google Calendar Setup

### Prerequisites
- Google account
- 10 minutes for first-time setup

### Setup Steps

#### 1. Create Google Cloud Project
1. Visit [Google Cloud Console](https://console.cloud.google.com/)
2. Click "New Project"
3. Name: `FamPilot` → Create

#### 2. Enable Google Calendar API
1. In your project, go to **APIs & Services** → **Library**
2. Search: `Google Calendar API`
3. Click **Enable**

#### 3. Configure OAuth Consent Screen
1. Go to **APIs & Services** → **OAuth consent screen**
2. User Type: **External** → Create
3. Fill in:
   - App name: `FamPilot`
   - User support email: Your email
   - Developer email: Your email
4. Save and Continue (skip scopes and test users)

#### 4. Create OAuth Credentials
1. Go to **APIs & Services** → **Credentials**
2. **+ CREATE CREDENTIALS** → **OAuth client ID**
3. Application type: **Desktop app**
4. Name: `FamPilot Desktop` → Create
5. **Download JSON** (download icon)
6. Rename to `credentials.json`
7. Move to FamPilot folder:
   ```bash
   mv ~/Downloads/client_secret_*.json credentials.json
   ```

#### 5. First Authentication
On first run with Google Calendar:
1. Browser opens automatically
2. Select your Google account
3. Click "Continue" (app not verified warning is normal)
4. Grant calendar permissions
5. `token.json` created for future use

**Security**: Both `credentials.json` and `token.json` are in `.gitignore`

## Code Structure

The codebase is organized into clear functional sections:

### OCR Extraction
- `extract_text_from_image()` - Tesseract OCR processing

### AI Event Extraction
- `extract_event_details()` - Claude API integration
- `clean_json_response()` - JSON parsing helper

### User Interaction
- `display_event_summary()` - Clean event display
- `confirm_event_with_user()` - Confirmation workflow
- `edit_event_fields()` - Interactive field editing
- `edit_field()` - Single field editor

### Date/Time Parsing
- `parse_time_range()` - Smart time parsing for various formats

### Google Calendar Integration
- `authenticate_google_calendar()` - OAuth2 authentication
- `create_calendar_event()` - Event creation

### Main Workflow
- `main()` - Orchestrates the entire process

## Command Options

```bash
# Basic usage
python3 main.py <image_path>

# Required environment variable
export ANTHROPIC_API_KEY='your-key'
```

## OCR Modes

FamPilot adapts to your environment:

### Local Development (with Tesseract)
```
Image → Tesseract OCR → Claude Text Analysis → Event Details
```
- **Fast**: ~2 seconds
- **Cheap**: ~$0.003 per image
- **Setup**: `brew install tesseract`

### Production/Cloud (without Tesseract)
```
Image → Claude Vision API → Event Details
```
- **Simple**: No installation needed
- **Works anywhere**: Render, Heroku, etc.
- **Cost**: ~$0.048 per image

**The app automatically detects which mode to use!**

See **[OCR_MODES.md](OCR_MODES.md)** for detailed comparison.

## Troubleshooting

### "No text extracted from image"
- Image quality too low
- Try a clearer image
- Note: Vision mode handles poor quality better than OCR

### "ANTHROPIC_API_KEY not set"
```bash
export ANTHROPIC_API_KEY='your-api-key'
```

### Slow Processing
- Vision API: ~4 seconds (normal)
- OCR mode: ~2 seconds
- Install Tesseract locally for faster processing

### "credentials.json not found"
- Google Calendar integration is optional
- App works without it, but won't add to calendar
- See Google Calendar Setup section above

### "This app isn't verified" (Google warning)
- Normal for personal apps
- Click **Advanced** → **Go to FamPilot (unsafe)**
- This is safe - it's your own app

### Authentication browser doesn't open
- Copy the URL from terminal
- Paste in browser manually

## Without Google Calendar

The app works perfectly without Google Calendar:
- Extracts and displays events normally
- Shows warning when trying to add to calendar
- Provides JSON output for manual entry
- All other features function normally

## File Structure

```
FamPilot/
├── main.py              # Main application
├── requirements.txt     # Python dependencies
├── README.md           # This file
├── SETUP_GUIDE.md      # Detailed setup guide
├── .gitignore          # Git ignore rules
├── credentials.json    # Google OAuth (you create this)
└── token.json         # Auto-generated (after first auth)
```

## Development

Built with:
- Python 3.7+
- Tesseract OCR
- Claude Sonnet 4.5 (Anthropic API)
- Google Calendar API v3
- PIL/Pillow for image processing

## Security

- Never commit `credentials.json` or `token.json`
- Never share API keys
- Both credential files are in `.gitignore`
- Rotate keys if exposed

## Support

For issues or questions:
1. Check the Troubleshooting section
2. Review SETUP_GUIDE.md for detailed instructions
3. Ensure all dependencies are installed
4. Verify environment variables are set

---

Built with ❤️ using Claude Code
