"""Stitch QMS screenshots into two readable strips.

    ./venv/bin/python make_preview.py

Produces screens/deploy.png  (setup story) and screens/workflow.png (daily use).
"""
import os
from PIL import Image, ImageDraw, ImageFont

D = "/home/mozaiz/workspace/qms/screens"

STRIPS = {
    "deploy": [
        ("1_login.png", "SIGN-IN — TAP YOUR ROLE"),
        ("2_setup.png", "SETUP — ADDRESS + QR TO PRINT"),
        ("3_setup_pos.png", "MANAGER — ADD POS COUNTERS"),
    ],
    "workflow": [
        ("4_scanner.png", "SCANNER — SCAN THE SO"),
        ("5_pos.png", "POS — CLAIM THE SO"),
        ("6_backstore.png", "BACKSTORE — DELIVER TO POS"),
    ],
}

GAP, PAD, TOP = 18, 22, 46


def font(sz):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(p, sz)
        except Exception:
            pass
    return ImageFont.load_default()


for name, panels in STRIPS.items():
    imgs = [Image.open(os.path.join(D, n)).convert("RGB") for n, _ in panels]
    titles = [t for _, t in panels]
    W = PAD * 2 + sum(i.width for i in imgs) + GAP * (len(imgs) - 1)
    H = PAD * 2 + TOP + max(i.height for i in imgs)
    sheet = Image.new("RGB", (W, H), "#0d1117")
    d = ImageDraw.Draw(sheet)
    f = font(21)
    x = PAD
    for im, t in zip(imgs, titles):
        d.text((x, PAD), t, fill="#58a6ff", font=f)
        sheet.paste(im, (x, PAD + TOP))
        x += im.width + GAP
    out = f"/home/mozaiz/workspace/qms/screens/{name}.png"
    sheet.save(out)
    print("wrote", out, sheet.size)
