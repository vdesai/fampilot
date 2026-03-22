# FamPilot Web Interface

Simple web interface for extracting event details from images.

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

Make sure you have all dependencies including:
- FastAPI
- Uvicorn
- Jinja2
- python-multipart

### 2. Set API Key

```bash
export ANTHROPIC_API_KEY='your-api-key-here'
```

### 3. Run the Server

```bash
python3 app.py
```

Or:

```bash
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

### 4. Open Browser

Navigate to: **http://localhost:8000**

## Usage

### Upload Image
1. Click the upload area or drag & drop an image
2. Supported formats: PNG, JPG, JPEG
3. Click "Extract Event Details"

### Review Details
The app will display:
- Title
- Start Date / End Date (for multi-day events)
- Time
- Location

### Actions

**Confirm** - Add event to Google Calendar (if configured) and show confirmation page

**Edit** - Modify any field inline:
- Click "Edit" button
- Update fields
- Click "Save Changes"

**Cancel** - Discard and return to upload page

## Architecture

```
FamPilot/
├── app.py                    # FastAPI backend
├── main.py                   # Core logic (reused)
├── templates/
│   ├── index.html           # Upload page
│   ├── result.html          # Event details & editing
│   └── confirmed.html       # Confirmation page
├── uploads/                  # Temporary image storage
└── requirements.txt          # Dependencies
```

## How It Works

```
User uploads image
       ↓
FastAPI receives file
       ↓
Save temporarily
       ↓
Call extract_text_from_image() [from main.py]
       ↓
Call extract_event_details() [from main.py]
       ↓
Display in result.html
       ↓
User: Confirm / Edit / Cancel
       ↓
If Confirm:
  - authenticate_google_calendar()
  - create_calendar_event()
  - Show confirmed.html
```

## API Endpoints

### `GET /`
- Renders upload page (index.html)

### `POST /upload`
- Receives image file
- Extracts text via OCR
- Gets event details from Claude
- Returns result page with event data

### `POST /edit/{event_id}`
- Updates event details
- Re-renders result page with updated data

### `POST /confirm/{event_id}`
- Authenticates with Google Calendar
- Creates calendar event
- Shows confirmation page

### `POST /cancel`
- Redirects back to home page

## Features

### Image Upload
- Click to upload
- Drag & drop support
- File type validation
- Visual feedback

### Event Display
- Clean, readable layout
- All fields clearly labeled
- Handles missing data gracefully (shows "Not found")

### Inline Editing
- Edit mode toggles without page reload
- All fields editable
- Date picker for dates
- Validation

### Google Calendar
- Only authenticates if user confirms
- Shows success/warning status
- Opens browser for OAuth (first time)
- Reuses saved credentials

### Responsive Design
- Works on desktop and mobile
- Clean gradient background
- Card-based layout
- Smooth animations

## Styling

Minimal, modern design:
- Purple gradient background
- White cards with shadows
- Clean typography
- Smooth transitions
- No external CSS frameworks

## Configuration

### Port
Default: `8000`

To change:
```python
# In app.py
uvicorn.run(app, host="0.0.0.0", port=YOUR_PORT)
```

Or via command line:
```bash
uvicorn app:app --port YOUR_PORT
```

### API Key
Required: `ANTHROPIC_API_KEY`

Set as environment variable:
```bash
export ANTHROPIC_API_KEY='your-key'
```

### Google Calendar (Optional)
- Place `credentials.json` in project root
- First run will authenticate via browser
- `token.json` created automatically

## Development

### Run with auto-reload:
```bash
uvicorn app:app --reload
```

### Debug mode:
```bash
uvicorn app:app --reload --log-level debug
```

## Deployment

### Local Network Access
```bash
python3 app.py
# Access from other devices: http://YOUR_IP:8000
```

### Production
For production deployment, consider:
- Use proper ASGI server (Gunicorn + Uvicorn)
- Add HTTPS
- Set up proper authentication
- Use database for event storage
- Add rate limiting
- Configure CORS if needed

Example production command:
```bash
gunicorn app:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

## Limitations

- In-memory storage (events cleared on restart)
- No user authentication
- No database persistence
- Single-user sessions
- Temporary file storage

These are intentional for simplicity. For production, add:
- Database (PostgreSQL/MongoDB)
- User authentication (OAuth2)
- Session management
- Persistent storage
- Rate limiting

## Troubleshooting

### "Address already in use"
```bash
# Kill process on port 8000
lsof -ti:8000 | xargs kill -9

# Or use different port
uvicorn app:app --port 8001
```

### "Module not found"
```bash
pip install -r requirements.txt
```

### "ANTHROPIC_API_KEY not set"
```bash
export ANTHROPIC_API_KEY='your-key'
```

### Images not processing
- Check Tesseract is installed: `tesseract --version`
- Verify image format is supported
- Check server logs for errors

### Google Calendar not working
- Web interface works without it
- See main README.md for setup
- Check credentials.json is in root directory

## Browser Compatibility

Tested on:
- Chrome/Edge (latest)
- Firefox (latest)
- Safari (latest)

Features used:
- Drag & drop API
- Fetch API
- CSS Grid/Flexbox
- Modern JavaScript (ES6+)

## Security Notes

- Don't expose to public internet without authentication
- Keep API keys secret
- Validate file uploads
- Sanitize user inputs
- Use HTTPS in production
- Set up CORS properly

## Performance

- Fast for single images (<2MB)
- Processing time: 3-5 seconds typically
- Depends on:
  - Image size
  - Text complexity
  - Claude API response time
  - Network speed

## Future Enhancements

Possible additions:
- [ ] User accounts
- [ ] Event history
- [ ] Batch processing
- [ ] Export to other calendars (Outlook, Apple)
- [ ] Mobile app
- [ ] Event templates
- [ ] Sharing events
- [ ] Recurring events
- [ ] Email notifications

---

Built with FastAPI and ❤️
