#!/usr/bin/env python3
"""
Create a test image with event information for testing
"""

from PIL import Image, ImageDraw, ImageFont

# Create a white image
width, height = 800, 600
image = Image.new('RGB', (width, height), 'white')
draw = ImageDraw.Draw(image)

# Add event text
text = """
SUMMER MUSIC FESTIVAL

Date: July 15, 2024
Time: 6:00 PM - 11:00 PM
Location: Central Park Amphitheater

Join us for an evening of great music!
"""

# Try to use a default font
try:
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 40)
    small_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 30)
except:
    font = ImageFont.load_default()
    small_font = ImageFont.load_default()

# Draw text on image
y_position = 100
for line in text.strip().split('\n'):
    if 'SUMMER MUSIC FESTIVAL' in line:
        draw.text((50, y_position), line, fill='black', font=font)
        y_position += 80
    else:
        draw.text((50, y_position), line, fill='black', font=small_font)
        y_position += 50

# Save image
image.save('test_event.png')
print("✅ Created test_event.png")
