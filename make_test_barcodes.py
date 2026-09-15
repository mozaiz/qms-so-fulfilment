"""Generate a printable test-barcode sheet for the QMS MVP."""
import qrcode
from barcode import Code128
from barcode.writer import ImageWriter
from PIL import Image, ImageDraw, ImageFont

CODES = ["RCPTCSQP01260915001", "CSQP01260915042"]
OUT = "/home/mozaiz/workspace/qms/test_barcodes.png"


def load_font(sz):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(p, sz)
        except Exception:
            pass
    return ImageFont.load_default()


bpaths = []
for i, code in enumerate(CODES, 1):
    p = "/tmp/bc128_%d" % i
    Code128(code, writer=ImageWriter()).save(p, {
        "module_width": 0.35, "module_height": 14.0, "font_size": 9,
        "text_distance": 3.0, "quiet_zone": 6.0, "write_text": True,
    })
    bpaths.append(p + ".png")

qpaths = []
for i, code in enumerate(CODES, 1):
    q = qrcode.QRCode(version=None, box_size=8, border=3,
                      error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(code)
    q.make(fit=True)
    img = q.make_image(fill_color="black", back_color="white").convert("RGB")
    p = "/tmp/qr_%d.png" % i
    img.save(p)
    qpaths.append(p)

W = 900
rows = []
for p in bpaths + qpaths:
    im = Image.open(p)
    rows.append(im.resize((W - 80, int(im.height * (W - 80) / im.width))))

H = 120 + sum(r.height + 60 for r in rows)
sheet = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(sheet)
f_title = load_font(30)
f_small = load_font(19)
f_lbl = load_font(21)

d.text((40, 26), "QMS — TEST BARCODE SHEET", fill="black", font=f_title)
d.text((40, 68), "Scan dengan phone. Setiap barcode = 1 queue ticket.", fill="#555555", font=f_small)

labels = [
    "CODE 128  ·  %s" % CODES[0],
    "CODE 128  ·  %s" % CODES[1],
    "QR CODE   ·  %s" % CODES[0],
    "QR CODE   ·  %s" % CODES[1],
]
y = 120
for r, lab in zip(rows, labels):
    d.text((40, y), lab, fill="#0d47a1", font=f_lbl)
    sheet.paste(r, (40, y + 30))
    y += r.height + 60

sheet.save(OUT, quality=95)
print("wrote", OUT, sheet.size)
