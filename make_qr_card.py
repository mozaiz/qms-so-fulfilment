"""Generate a printable A4 sign for the store wall.

    ./venv/bin/python make_qr_card.py                    # uses the port in qms.env
    ./venv/bin/python make_qr_card.py http://localhost:8099

One sheet per outlet: big QR, the address in plain text underneath, and three
steps. Nothing else for staff to remember.

Outputs qr_card_<STORE>.png and .pdf next to this script.
"""
import io
import json
import os
import re
import sys
import urllib.request

import qrcode
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))


def default_base():
    """The port this install actually listens on, not a hardcoded 8099.

    Run on a box installed on a non-default port, a hardcoded default quietly
    prints a wall sign pointing at nothing.
    """
    port = "8099"
    try:
        with open(os.path.join(HERE, "qms.env"), encoding="utf-8") as fh:
            m = re.search(r"^QMS_PORT=\s*['\"]?(\d+)", fh.read(), re.M)
            if m:
                port = m.group(1)
    except OSError:
        pass
    return f"http://127.0.0.1:{port}"


BASE = (sys.argv[1] if len(sys.argv) > 1 else default_base()).rstrip("/")

DPI = 150
W, H = int(8.27 * DPI), int(11.69 * DPI)      # A4 portrait
BG = (255, 255, 255)
INK = (13, 17, 23)
DIM = (95, 105, 120)
ACCENT = (47, 129, 247)


def font(size, bold=False):
    names = ("DejaVuSans-Bold.ttf",) if bold else ("DejaVuSans.ttf",)
    for n in names:
        for p in (f"/usr/share/fonts/truetype/dejavu/{n}",
                  f"/Library/Fonts/{n}",
                  f"C:/Windows/Fonts/{n}"):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def centred(d, text, y, f, fill, width=W, x0=0):
    bb = d.textbbox((0, 0), text, font=f)
    d.text((x0 + (width - (bb[2] - bb[0])) / 2 - bb[0], y), text, fill=fill, font=f)


def main():
    try:
        with urllib.request.urlopen(BASE + "/api/network", timeout=10) as r:
            net = json.load(r)
    except Exception as e:
        print(f"Cannot reach QMS at {BASE}: {e}")
        print("Start it first, or pass the address: make_qr_card.py http://host:port")
        sys.exit(1)

    lan = net.get("lan_urls") or []
    # prefer a normal private LAN address over a tailscale/VPN one
    private = [u for u in lan if any(s in u for s in ("//192.168.", "//10.", "//172."))]
    url = (private or lan or [net["localhost_url"]])[0]

    store = net.get("store_name") or "Machines"
    code = net.get("store_code") or ""

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    M = 90

    centred(d, "QMS", 78, font(96, True), ACCENT)
    centred(d, "SO Fulfilment", 186, font(40, True), INK)
    d.line([(M, 258), (W - M, 258)], fill=(220, 226, 234), width=3)
    centred(d, store, 282, font(34, True), INK)

    y = 356
    centred(d, "Scan this to open the app", y, font(30), DIM)
    y += 62

    qr = qrcode.QRCode(version=None, box_size=10, border=2,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    qimg = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    side = 660
    qimg = qimg.resize((side, side), Image.NEAREST)
    frame = Image.new("RGB", (side + 28, side + 28), (255, 255, 255))
    frame.paste(qimg, (14, 14))
    fd = ImageDraw.Draw(frame)
    fd.rectangle([0, 0, frame.width - 1, frame.height - 1], outline=INK, width=4)
    img.paste(frame, ((W - frame.width) // 2, y))
    y += frame.height + 46

    f_addr = font(38, True)
    bb = d.textbbox((0, 0), url, font=f_addr)
    while bb[2] - bb[0] > W - 2 * M and f_addr.size > 18:
        f_addr = font(f_addr.size - 2, True)
        bb = d.textbbox((0, 0), url, font=f_addr)
    centred(d, url, y, f_addr, INK)
    y += (bb[3] - bb[1]) + 34

    centred(d, f"Store code: {code}", y, font(22), DIM)
    y += 74

    # ---- steps on one dark card ----
    card_h = 300
    d.rounded_rectangle([M, y, W - M, y + card_h], radius=24, fill=(244, 247, 250),
                        outline=(214, 222, 232), width=2)
    steps = [
        "1.  Scan the code above, or type the address",
        "2.  Tap your role  (SCANNER / POS 1-4 / BACKSTORE)",
        "3.  Scanner: tap Start Scanning and point at the SO barcode",
    ]
    f_step = font(27)
    sy = y + 44
    for s in steps:
        d.text((M + 40, sy), s, fill=INK, font=f_step)
        sy += 62

    y += card_h + 40

    # ---- the one caveat that actually matters ----
    note = ("Camera only works on the computer running QMS (or over HTTPS). "
            "On other devices, use Manual Entry.")
    f_note = font(23)
    words = note.split()
    line, lines = "", []
    for wd in words:
        t = (line + " " + wd).strip()
        if d.textbbox((0, 0), t, font=f_note)[2] > W - 2 * M:
            lines.append(line)
            line = wd
        else:
            line = t
    lines.append(line)
    for i, ln in enumerate(lines):
        centred(d, ln, y + i * 32, f_note, DIM)

    centred(d, f"QMS v{net.get('version','?')}  ·  {net.get('hostname','')}",
            H - 78, font(20), (160, 170, 182))

    safe = "".join(c for c in code if c.isalnum()) or "store"
    png = os.path.join(HERE, f"qr_card_{safe}.png")
    pdf = os.path.join(HERE, f"qr_card_{safe}.pdf")
    img.save(png, dpi=(DPI, DPI))
    img.save(pdf, "PDF", resolution=DPI)
    print("wrote", png)
    print("wrote", pdf)
    print("QR points at:", url)


if __name__ == "__main__":
    main()
