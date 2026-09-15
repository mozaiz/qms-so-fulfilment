"""Combine QMS screen captures into a compact preview grid."""
import os
from PIL import Image, ImageDraw, ImageFont

D = "/home/mozaiz/workspace/qms/screens"
PANELS = [
    ("1_login.png", "LOGIN — PILIH ROLE"),
    ("2_scanner_scan.png", "SCANNER — SCAN SO"),
    ("4_pos_ambill.png", "POS — AMBIL SO"),
    ("6_backstore.png", "BACKSTORE — HANTAR KE POS"),
    ("8_manager_stats.png", "MANAGER — STATS"),
]
COLS = 3

imgs = [Image.open(os.path.join(D, n)).convert("RGB") for n, _ in PANELS]
titles = [t for _, t in PANELS]

PAD, GAP, TOP, TGAP = 22, 18, 46, 34
cw = max(i.width for i in imgs)
ch = max(i.height for i in imgs)
rows = (len(imgs) + COLS - 1) // COLS

W = PAD * 2 + COLS * cw + (COLS - 1) * GAP
H = PAD * 2 + rows * (TOP + ch) + (rows - 1) * TGAP
sheet = Image.new("RGB", (W, H), "#0d1117")
d = ImageDraw.Draw(sheet)


def font(sz):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(p, sz)
        except Exception:
            pass
    return ImageFont.load_default()


f = font(21)
for idx, (im, t) in enumerate(zip(imgs, titles)):
    r, c = divmod(idx, COLS)
    x = PAD + c * (cw + GAP)
    y = PAD + r * (TOP + ch + TGAP)
    d.text((x, y), t, fill="#58a6ff", font=f)
    sheet.paste(im, (x, y + TOP))

out = "/home/mozaiz/workspace/qms/screens/preview.png"
sheet.save(out)
print("wrote", out, sheet.size)
