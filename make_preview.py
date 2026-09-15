"""Stitch QMS screenshots into readable strips.

    ./venv/bin/python make_preview.py

Produces screens/handover.png (the four-stage flow) and screens/deploy.png.
"""
import os
from PIL import Image, ImageDraw, ImageFont

D = "/home/mozaiz/workspace/qms/screens"

STRIPS = {
    "handover": [
        ("1_pos_queue.png", "POS — QUEUE (unclaimed, oldest first)"),
        ("2_pos_mine.png", "POS — MY SOs (Pending Stock)"),
        ("4_pos2_delivered.png", "POS — MY SOs (Delivered, ready)"),
        ("6_atcounter.png", "BACKSTORE — AT COUNTER"),
    ],
}

GAP, PAD, TOP, TGAP = 18, 22, 46, 34


def font(sz):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(p, sz)
        except Exception:
            pass
    return ImageFont.load_default()


import sys
want = sys.argv[1:] or list(STRIPS)

for name in want:
    panels = [p for p in STRIPS.get(name, []) if os.path.exists(os.path.join(D, p[0]))]
    if not panels:
        print("skip", name, "(screenshots missing)")
        continue
    imgs = [Image.open(os.path.join(D, n)).convert("RGB") for n, _ in panels]
    titles = [t for _, t in panels]

    # 2 columns keeps the sheet a sensible shape for a phone chat window
    cols = 2 if len(imgs) > 2 else len(imgs)
    rows = (len(imgs) + cols - 1) // cols
    cw = max(i.width for i in imgs)
    ch = max(i.height for i in imgs)
    W = PAD * 2 + cols * cw + (cols - 1) * GAP
    H = PAD * 2 + rows * (TOP + ch) + (rows - 1) * TGAP
    sheet = Image.new("RGB", (W, H), "#0d1117")
    d = ImageDraw.Draw(sheet)
    f = font(19)
    for idx, (im, t) in enumerate(zip(imgs, titles)):
        r, c = divmod(idx, cols)
        x = PAD + c * (cw + GAP)
        y = PAD + r * (TOP + ch + TGAP)
        d.text((x, y), t, fill="#58a6ff", font=f)
        sheet.paste(im, (x, y + TOP))
    out = f"{D}/{name}.png"
    sheet.save(out)
    print("wrote", out, sheet.size)
