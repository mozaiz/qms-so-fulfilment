"""Generate the printable setup guide for an outlet — A4, two sides.

    ./venv/bin/python make_install_guide.py            # reads QMS's own addresses
    ./venv/bin/python make_install_guide.py --blank    # leave addresses to fill in

Writes qms_install_guide.pdf (and .png previews) next to this script.

Why a PDF and not the README
----------------------------
The person setting this up at an outlet has never seen the repository and never
will. They have a computer, a phone, and about ten minutes. Everything they need
has to be on one sheet in front of them, in the order they need it, with the
exact lines to paste — and it has to say the things that are obvious only after
you already know them:

  * this goes on the POS 1 computer, which then IS the server and must stay on
  * P1 to P4 all open the SAME address; only the role button differs
  * a phone camera needs a certificate installed once, and on an iPhone that
    means a second switch in Settings that everyone misses

It is generated on the store's own box so the addresses and QR codes are real.
"""

import io
import json
import os
import re
import sys
import urllib.request

try:
    import qrcode
except ImportError:
    print("qrcode is required: ./venv/bin/pip install qrcode pillow")
    raise SystemExit(1)

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))

DPI = 150
W, H = int(8.27 * DPI), int(11.69 * DPI)          # A4 portrait
M = 76                                            # page margin

BG = (255, 255, 255)
INK = (13, 17, 23)
DIM = (95, 105, 120)
LINE = (222, 227, 235)
ACCENT = (47, 129, 247)
ACCENT_BG = (238, 245, 255)
GOOD = (26, 127, 55)
GOOD_BG = (235, 248, 239)
WARN = (154, 103, 0)
WARN_BG = (255, 248, 230)
WARN_LINE = (240, 224, 176)
CODE_BG = (244, 246, 249)
CODE_LINE = (214, 220, 230)

MONO_CANDIDATES = ("DejaVuSansMono.ttf", "LiberationMono-Regular.ttf")
SANS_CANDIDATES = ("DejaVuSans.ttf", "LiberationSans-Regular.ttf")
BOLD_CANDIDATES = ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf")

FONT_DIRS = ("/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/truetype/liberation/",
             "/Library/Fonts/", "/System/Library/Fonts/Supplemental/",
             "C:/Windows/Fonts/")

_cache = {}


def _load(candidates, size):
    key = (candidates[0], size)
    if key in _cache:
        return _cache[key]
    for name in candidates:
        for d in FONT_DIRS:
            try:
                f = ImageFont.truetype(d + name, size)
                _cache[key] = f
                return f
            except Exception:
                continue
    f = ImageFont.load_default()
    _cache[key] = f
    return f


def sans(size, bold=False):
    return _load(BOLD_CANDIDATES if bold else SANS_CANDIDATES, size)


def mono(size, bold=False):
    return _load(MONO_CANDIDATES, size)


class Page:
    """A flowing A4 page. Keeps a cursor so blocks stack without hand-tuned y."""

    def __init__(self):
        self.img = Image.new("RGB", (W, H), BG)
        self.d = ImageDraw.Draw(self.img)
        self._y = M
        self.max_y = M

    # The cursor is a property so every advance is recorded. Tracking it by hand
    # in each block method is exactly how one of them gets forgotten and a page
    # silently runs past the bottom of the sheet.
    @property
    def y(self):
        return self._y

    @y.setter
    def y(self, value):
        self._y = value
        if value > self.max_y:
            self.max_y = value

    # ---------------------------------------------------------------- text
    def text(self, s, f, fill=INK, x=M, y=None, width=None):
        y = self.y if y is None else y
        self.d.text((x, y), s, font=f, fill=fill)
        return self.d.textbbox((x, y), s, font=f)

    def centre(self, s, f, fill=INK, y=None, width=None, x0=M):
        y = self.y if y is None else y
        width = width or (W - 2 * M)
        bb = self.d.textbbox((0, 0), s, font=f)
        self.d.text((x0 + (width - (bb[2] - bb[0])) / 2 - bb[0], y), s, fill=fill, font=f)
        return bb

    def para(self, s, f, fill=INK, x=None, width=None, lh=None, y=None):
        """Word-wrapped paragraph. Returns the y it finished at."""
        x = M if x is None else x
        width = width or (W - 2 * M)
        lh = lh or (f.size + 7)
        y = self.y if y is None else y
        for line in wrap(self.d, s, f, width):
            self.d.text((x, y), line, font=f, fill=fill)
            y += lh
        self.y = y
        return y

    def rule(self, gap=14, colour=LINE):
        self.y += gap
        self.d.line((M, self.y, W - M, self.y), fill=colour, width=2)
        self.y += gap

    def space(self, n=14):
        self.y += n

    # --------------------------------------------------------------- blocks
    def band(self, text, f, fg, bg, note=None, fnote=None, pad=20):
        """A full-width coloured bar. Used for the loud things."""
        h = pad * 2 + f.size + (fnote.size + 8 if note else 0)
        self.d.rounded_rectangle((M, self.y, W - M, self.y + h), radius=12, fill=bg)
        y = self.y + pad - 4
        self.centre(text, f, fg, y=y)
        if note:
            self.centre(note, fnote, fg, y=y + f.size + 8)
        self.y += h

    def code(self, lines, f=None, pad=16, x=None, width=None, label=None, flabel=None):
        """A monospace box holding something to be copied or typed."""
        f = f or mono(23)
        x = M if x is None else x
        width = width or (W - 2 * M)
        lh = f.size + 9
        top = self.y
        if label:
            flabel = flabel or sans(17, True)
            self.d.text((x, self.y), label, font=flabel, fill=DIM)
            self.y += flabel.size + 8
        body = self.y
        h = pad * 2 + lh * len(lines)
        self.d.rounded_rectangle((x, body, x + width, body + h), radius=10,
                                 fill=CODE_BG, outline=CODE_LINE, width=2)
        yy = body + pad - 3
        for ln in lines:
            self.d.text((x + pad, yy), ln, font=f, fill=INK)
            yy += lh
        self.y = body + h

    def callout(self, title, body, f=None, ftitle=None, fg=WARN, bg=WARN_BG, border=WARN_LINE):
        f = f or sans(21)
        ftitle = ftitle or sans(23, True)
        lines = wrap(self.d, body, f, W - 2 * M - 40)
        lh = f.size + 7
        h = 22 + ftitle.size + 10 + lh * len(lines)
        self.d.rounded_rectangle((M, self.y, W - M, self.y + h), radius=12,
                                 fill=bg, outline=border, width=2)
        yy = self.y + 16
        self.d.text((M + 20, yy), title, font=ftitle, fill=fg)
        yy += ftitle.size + 10
        for ln in lines:
            self.d.text((M + 20, yy), ln, font=f, fill=fg)
            yy += lh
        self.y += h

    def step(self, n, title, f=None):
        """A numbered heading with a filled circle."""
        f = f or sans(29, True)
        r = 19
        cy = self.y + f.size / 2
        self.d.ellipse((M, cy - r, M + 2 * r, cy + r), fill=INK)
        bb = self.d.textbbox((0, 0), str(n), font=sans(21, True))
        self.d.text((M + r - (bb[2] - bb[0]) / 2 - bb[0], cy - (bb[3] - bb[1]) / 2 - bb[1]),
                    str(n), font=sans(21, True), fill=(255, 255, 255))
        self.d.text((M + 2 * r + 16, self.y), title, font=f, fill=INK)
        self.y += f.size + 14

    def qtile(self, url, size, right_lines=None, right_rows=None, gap=26,
              label=None, caption=None):
        """QR on the left, text on the right, in one block.

        Everything is placed from a fixed top so the two columns cannot drift
        into each other, and the cursor advances once past the taller one.
        """
        top = self.y
        if label:
            self.d.text((M, top), label, font=sans(18, True), fill=DIM)
            top += 26
        img = qrcode.make(url, box_size=10, border=1).convert("RGB").resize((size, size),
                                                                          Image.NEAREST)
        self.img.paste(img, (M, top))
        if caption:
            self.d.text((M, top + size + 8), caption, font=sans(16), fill=DIM)

        x = M + size + gap
        yy = top
        for ln in (right_lines or []):
            f = mono(20)
            self.d.rounded_rectangle((x, yy, W - M, yy + f.size + 18), radius=8,
                                     fill=CODE_BG, outline=CODE_LINE, width=2)
            self.d.text((x + 14, yy + 7), ln, font=f, fill=INK)
            yy += f.size + 18 + 10
        for a, b in (right_rows or []):
            self.d.text((x, yy), a, font=sans(20, True), fill=INK)
            self.d.text((x + 168, yy), b, font=sans(19), fill=DIM)
            yy += 27

        bottom = max(top + size + (26 if caption else 0), yy)
        self.y = bottom
        self.max_y = max(self.max_y, bottom)

    def qr(self, url, size=200, x=None, label=None, caption=None):
        img = qrcode.make(url, box_size=10, border=1).convert("RGB").resize((size, size),
                                                                            Image.NEAREST)
        x = M if x is None else x
        if label:
            self.d.text((x, self.y), label, font=sans(18, True), fill=DIM)
            self.y += 26
        self.img.paste(img, (x, self.y))
        if caption:
            self.d.text((x, self.y + size + 8), caption, font=sans(16), fill=DIM)
            self.y += size + 30
        else:
            self.y += size + 12

    # -------------------------------------------------------------- output
    def finish(self, page_no=None, total=None):
        if page_no:
            f = sans(16)
            s = f"Page {page_no} of {total}" if total else f"Page {page_no}"
            bb = self.d.textbbox((0, 0), s, font=f)
            self.d.text((W - M - (bb[2] - bb[0]), H - 54), s, font=f, fill=DIM)
        # Nothing may collide with the footer or run off the sheet. This is a
        # printed page nobody can scroll, and a cropped instruction is worse than
        # no instruction at all — so fail here rather than at the printer.
        limit = H - 70
        if self.max_y > limit:
            raise SystemExit(
                f"page overflows: content reaches y={self.max_y:.0f}, "
                f"safe limit is {limit} (page height {H})")
        return self.img


def wrap(d, text, f, width):
    """Word wrap that measures with the real font."""
    words = text.split()
    if not words:
        return [""]
    lines, cur = [], words[0]
    for w in words[1:]:
        trial = cur + " " + w
        if d.textbbox((0, 0), trial, font=f)[2] <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def pick_lan(urls):
    """A normal store-wifi address beats a tailscale or VPN one."""
    priv = [u for u in urls if any(s in u for s in ("//192.168.", "//10.", "//172."))]
    return (priv or urls or [""])[0]


def fetch_network():
    """Ask the running server for the real addresses. Falls back to the box."""
    port = "8099"
    try:
        with open(os.path.join(HERE, "qms.env"), encoding="utf-8") as fh:
            env = fh.read()
        m = re.search(r"^QMS_PORT=\s*['\"]?(\d+)", env, re.M)
        if m:
            port = m.group(1)
        m2 = re.search(r"^QMS_TLS_PORT=\s*['\"]?(\d+)", env, re.M)
    except OSError:
        m2 = None
    tls_port = m2.group(1) if m2 else "8443"
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/network", timeout=6) as r:
            net = json.load(r)
        return net, port, net.get("tls_port", tls_port)
    except Exception:
        pass
    # Server not running: work it out locally so the sheet can still be printed.
    try:
        import netinfo
        ips = netinfo.local_ipv4()
        priv = [i for i in ips if i.startswith(("192.168.", "10.", "172."))]
        ip = (priv or ips or ["127.0.0.1"])[0]
        return ({"store_name": "", "store_code": "", "lan_urls": [f"http://{i}:{port}" for i in ips]},
                port, tls_port)
    except Exception:
        return ({"store_name": "", "store_code": "", "lan_urls": []}, port, tls_port)


def blank(url, port, kind):
    """Addresses to fill in by hand, when the sheet is printed before setup."""
    if kind == "web":
        u = url.replace(f":{port}", "").replace("http://", "").replace("https://", "")
        return f"http://{u.split(':')[0]}:____"
    return "https://____________:8443"


# --------------------------------------------------------------------- pages
def page_one(net, lan_url, blank_mode, store):
    p = Page()
    p.d.rectangle((0, 0, W, 12), fill=ACCENT)

    p.y = M - 6
    p.centre("QMS", sans(80, True), ACCENT)
    p.y += 86
    p.centre("Setting up the POS 1 computer", sans(34, True))
    p.y += 46
    p.centre(f"Machines{('  ·  ' + store) if store else ''}", sans(20), DIM)
    p.y += 26

    p.band("DO THIS ON THE POS 1 COMPUTER",
           sans(34, True), (122, 78, 0), WARN_BG,
           note="It becomes the server for the whole store. It must stay switched on all day.",
           fnote=sans(21))

    p.space(24)
    p.callout("Before you start",
              "POS 1 is the only computer you set up. Every other counter and phone "
              "connects to it over the store wifi — nothing is installed on them.",
              fg=(45, 62, 90), bg=ACCENT_BG, border=(190, 214, 245))

    p.space(22)
    p.step(1, "Open the Terminal on POS 1")
    p.para("Mac:  press  ⌘ + Space,  type  Terminal,  press Enter.  A black window opens.",
           sans(22), DIM)
    p.space(20)

    p.step(2, "Install — copy this line, paste it, press Enter")
    line = "curl -fsSL https://raw.githubusercontent.com/mozaiz/qms-so-fulfilment/main/install.sh | bash"
    p.space(8)
    # wrap the one-liner across two box lines; staff must be able to read it
    f = mono(19)
    chunks = wrap(p.d, line, f, W - 2 * M - 32)
    p.code(chunks, f=f)
    p.space(12)
    p.para("Takes 2–4 minutes. Wait until it says Done. Do not close the window.",
           sans(21), DIM)
    p.space(20)

    p.step(3, "Start it")
    p.space(8)
    p.code(['D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; sh "$D/qms.sh" restart'], f=mono(19))
    p.space(12)
    p.para("If it is already running this simply restarts it. Nothing is lost.",
           sans(21), DIM)
    p.space(14)

    p.step(4, "Open this address on every counter")
    p.space(6)
    p.callout("P1, P2, P3, P4 and Backstore all use the SAME address.",
              "Only the button they tap on the sign-in screen is different.",
              fg=INK, bg=GOOD_BG, border=(178, 222, 190))
    p.space(16)

    shown = "http://____________:____" if blank_mode else lan_url
    p.qtile("http://192.168.0.0:8099/" if blank_mode else shown,
            size=156,
            label="QR — scan with a phone",
            right_lines=[shown],
            right_rows=[("P1  ·  POS 1", "tap  P1"), ("P2  ·  POS 2", "tap  P2"),
                        ("P3  ·  POS 3", "tap  P3"), ("P4  ·  POS 4", "tap  P4"),
                        ("Backstore", "tap  BACKSTORE")])
    p.space(16)
    p.rule()
    p.d.text((M, p.y), "If something goes wrong", font=sans(26, True), fill=INK)
    p.y += 40
    for line in [
        "“command not found” — the line was cut short. Copy step 2 again, in one go.",
        "It says Python is missing — run this, then repeat step 2:",
        "Counters cannot reach it — both macOS prompts must be Allow.",
        "Anything else — run this and send the printout to your manager:",
    ]:
        p.para("•  " + line, sans(20), INK, width=W - 2 * M - 10, lh=28)
        p.space(3)
        if "Python is missing" in line:
            p.code(["xcode-select --install"], f=mono(21))
            p.space(8)
        if "Anything else" in line:
            p.code(['D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; sh "$D/qms.sh" doctor'],
                   f=mono(19))
            p.space(8)

    p.space(2)
    p.para("Scanner (the camera) is page 2. On this computer the camera already works — "
           "you can scan from POS 1 itself without setting anything up.",
           sans(21), DIM, lh=30)
    return p.finish(1, 2)


def page_two(net, lan_url, tls_url, blank_mode, store):
    p = Page()
    p.d.rectangle((0, 0, W, 12), fill=ACCENT)

    p.y = M - 6
    p.centre("QMS", sans(64, True), ACCENT)
    p.y += 70
    p.centre("Using a phone as the SCANNER", sans(34, True))
    p.y += 46
    p.centre("One minute per phone. Needed once — after that the camera works for good.",
             sans(21), DIM)
    p.y += 26

    p.callout("Why the phone needs this",
              "A browser only gives a page a camera on a secure address. Plain "
              "http://192.168… is not one, so the phone must first trust this store's "
              "certificate. Do this once per phone.",
              fg=(45, 62, 90), bg=ACCENT_BG, border=(190, 214, 245))
    p.space(20)

    p.step(1, "On the phone, open this")
    p.space(6)
    setup_url = "http://____________:8099/setup/phone" if blank_mode \
        else lan_url.rstrip("/") + "/setup/phone"
    p.qtile("http://192.168.0.0:8099/setup/phone" if blank_mode else setup_url,
            size=150, label="QR — scan with the phone",
            right_lines=[setup_url], gap=24)
    p.space(16)

    p.step(2, "Install the certificate")
    p.space(6)
    p.callout("iPhone / iPad — TWO steps, do not stop after the first",
              "1.  Tap Install certificate → Settings → Profile Downloaded → Install.\n"
              "2.  Settings → General → About → Certificate Trust Settings → "
              "turn ON  QMS Store Certificate Authority.",
              fg=(122, 78, 0), bg=WARN_BG, border=WARN_LINE)
    p.space(14)
    p.callout("Android",
              "1.  Tap Download certificate → open the file → name it  QMS  → "
              "choose  CA certificate  → confirm your screen lock.\n"
              "2.  If Android refuses it: open chrome://flags , search "
              "unsafely-treat-insecure-origin-as-secure , paste the http address from "
              "step 1, relaunch Chrome.",
              fg=(45, 62, 90), bg=ACCENT_BG, border=(190, 214, 245))
    p.space(20)

    p.step(3, "Then scan on this address")
    p.space(6)
    secure = "https://____________:8443" if blank_mode else tls_url
    p.qtile("https://192.168.0.0:8443" if blank_mode else secure,
            size=150, label="QR — the scanner address",
            right_lines=[secure], gap=24)
    p.space(10)
    p.para("Tap  SCANNER  and allow the camera when the phone asks.", sans(22), DIM)
    p.space(18)

    p.callout("You are done when the phone shows the camera",
              "Tapping SCANNER gives a live camera view and a green Start camera button. "
              "Scan a real SO number once to be sure — it should answer SCANNED ✓.",
              fg=(16, 100, 44), bg=GOOD_BG, border=(178, 222, 190))
    p.space(18)

    p.rule()
    p.d.text((M, p.y), "Only the SCANNER needs any of this", font=sans(25, True), fill=INK)
    p.y += 38
    p.para("P1 to P4 and Backstore just open the plain http:// address from page 1. "
           "No certificate, no camera, nothing to install on those devices.",
           sans(20), DIM, lh=28)
    p.space(14)

    p.rule()
    p.d.text((M, p.y), "If the camera does not come out", font=sans(26, True), fill=INK)
    p.y += 40
    for line in [
        "Are you on the https:// address? The camera can never work on http://192.168…",
        "iPhone: is the switch ON in Settings → General → About → Certificate Trust Settings?",
        "Is the phone on the store wifi, not on mobile data?",
        "Still stuck? The store computer's address may have changed — restart QMS on POS 1.",
    ]:
        p.para("•  " + line, sans(20), INK, width=W - 2 * M - 10, lh=29)
        p.space(6)
    return p.finish(2, 2)


def main():
    blank_mode = "--blank" in sys.argv

    net, port, tls_port = fetch_network()
    store = net.get("store_name") or ""

    # --url lets a sheet be made for a machine that is not this one, or before it
    # exists at all — useful for planning an outlet rollout from head office.
    override = None
    for i, a in enumerate(sys.argv):
        if a == "--url" and i + 1 < len(sys.argv):
            override = sys.argv[i + 1].rstrip("/")
        if a == "--tls-port" and i + 1 < len(sys.argv):
            tls_port = sys.argv[i + 1]
        if a == "--store" and i + 1 < len(sys.argv):
            store = sys.argv[i + 1]

    if override:
        host = override.split("//")[-1].split(":")[0]
        lan_url = override
        tls_url = f"https://{host}:{tls_port}"
    else:
        lan_url = pick_lan(net.get("lan_urls") or [])
        if not lan_url:
            lan_url = f"http://192.168.0.0:{port}"
        tls_url = pick_lan(net.get("https_urls") or []) or \
            f"https://{lan_url.split('//')[-1].split(':')[0]}:{tls_port}"

    print("==> QMS installation guide")
    print(f"    store    : {store or '(not set)'}")
    print(f"    counters : {lan_url}")
    print(f"    scanner  : {tls_url}")
    if blank_mode:
        print("    addresses left blank to fill in by hand")

    pages = [
        page_one(net, lan_url, blank_mode, store),
        page_two(net, lan_url, tls_url, blank_mode, store),
    ]

    pdf = os.path.join(HERE, "qms_install_guide.pdf")
    pages[0].save(pdf, "PDF", resolution=DPI, save_all=True, append_images=pages[1:])

    for i, im in enumerate(pages, 1):
        im.save(os.path.join(HERE, f"qms_install_guide_p{i}.png"), "PNG")

    print(f"\n    written: qms_install_guide.pdf  (2 pages, A4)")
    print(f"    preview: qms_install_guide_p1.png, qms_install_guide_p2.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
