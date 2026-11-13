#!/usr/bin/env python3
"""Draw the Google Play feature graphic (1024x500, 24-bit PNG without alpha).

Usage: python3 play/make-feature-graphic.py ROUNDEL_PNG FONT OUT_PNG
ROUNDEL_PNG is website/public/brand/roundel-512.png. FONT is the site's display face,
Bricolage Grotesque (variable), for example from website/node_modules/@fontsource-variable/
bricolage-grotesque/files/bricolage-grotesque-latin-wght-normal.woff2. Needs Pillow.
"""
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

roundel, font_path, out = sys.argv[1:4]
W, H = 1024, 500
img = Image.new("RGB", (W, H), "#07061a")
glow = Image.new("RGB", (W, H), "#07061a")
d = ImageDraw.Draw(glow)
d.ellipse((10, 30, 420, 470), fill="#3a2f99")
d.ellipse((620, 260, 1180, 760), fill="#1b1650")
img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(110)), 0.9)
r = Image.open(roundel).convert("RGBA").resize((280, 280), Image.LANCZOS)
img.paste(r, (72, 110), r)
draw = ImageDraw.Draw(img)
def font(size, weight):
    f = ImageFont.truetype(font_path, size)
    try:
        f.set_variation_by_axes([weight] if len(f.get_variation_axes()) == 1 else [weight, 100, size if size < 96 else 96][: len(f.get_variation_axes())])
    except Exception as e:
        print("variation:", e)
    return f
lines = [((404, 132), "Raticode", font(104, 800), "#fdf2dc"),
         ((408, 262), "Rem in your pocket.", font(44, 600), "#b9b1ff"),
         ((410, 324), "Chat, voice and tasks on your own computer.", font(25, 450), "#d9d2c4")]
for (x, y), text, f, colour in lines:
    right = x + draw.textlength(text, font=f)
    assert right <= W - 56, (text, right)
    draw.text((x, y), text, font=f, fill=colour)
img.save(out, optimize=True)
print(img.mode, img.size)
