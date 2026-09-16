"""Check the printable setup guide is actually printable and actually correct.

The guide goes on a wall at an outlet. Nobody there can debug it, nobody will
notice a cropped instruction until a customer is waiting, and a QR code pointing
at the wrong address is worse than no QR at all — it sends staff somewhere that
does not answer.

None of that is visible in a diff, so it is checked here:

  * the page does not overflow (the generator raises, this proves it does not)
  * nothing bleeds into the print margins
  * every QR code DECODES, and decodes to the address it is supposed to
  * the sheet is not half empty — the content reaches the bottom of the page
  * the lines staff have to paste are the real ones, not plausible-looking ones

    ./venv/bin/python deploy/test_guide.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PY = sys.executable

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  -- {detail}" if detail else ""))


try:
    from PIL import Image, ImageOps
except ImportError:
    print("Pillow is required (it is in requirements.txt)")
    raise SystemExit(1)

try:
    import zxingcpp
    HAS_ZXING = True
except ImportError:
    HAS_ZXING = False

SRC = open(os.path.join(REPO, "make_install_guide.py"), encoding="utf-8").read()

TMP = tempfile.mkdtemp(prefix="qms-guide-")
for f in ("make_install_guide.py", "netinfo.py", "qms.env.example"):
    shutil.copy(os.path.join(REPO, f), TMP)

print("=" * 64)
print("The printed setup guide")
print("=" * 64)


def generate(*args):
    return subprocess.run([PY, "make_install_guide.py", *args], cwd=TMP,
                          capture_output=True, text=True, timeout=300)


def qrs(png):
    if not HAS_ZXING:
        return None
    return sorted(b.text for b in zxingcpp.read_barcodes(Image.open(png)))


def ink_bands(png, tenths=10):
    im = Image.open(png).convert("L")
    W, H = im.size
    return [int(sum(im.crop((0, int(i * H / tenths), W, int((i + 1) * H / tenths)))
                    .histogram()[:200]) / 1000) for i in range(tenths)], (W, H)


print("\n== it generates ==")
r = generate()
check("the generator runs", r.returncode == 0, (r.stdout + r.stderr).strip().splitlines()[-1][:80])
pdf = os.path.join(TMP, "qms_install_guide.pdf")
check("it writes a PDF", os.path.exists(pdf), f"{os.path.getsize(pdf)} bytes" if os.path.exists(pdf) else "missing")

print("\n== the pages fit A4 and do not bleed off the sheet ==")
pages = sorted(f for f in os.listdir(TMP) if re.match(r"qms_install_guide_p\d+\.png$", f))
check("two pages", len(pages) == 2, ", ".join(pages))
if len(pages) != 2:
    # A generator that refuses to write (its own overflow guard) must be reported
    # as a failure with the reason, not crash the suite on a missing file.
    print("\n  the generator did not produce two pages — stopping here.")
    print("  reason:", (r.stdout + r.stderr).strip().splitlines()[-1][:110])
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nPASSED {len(PASS)} / {len(PASS) + len(FAIL)}")
    sys.exit(1)
for i, png in enumerate(pages, 1):
    path = os.path.join(TMP, png)
    im = Image.open(path).convert("L")
    W, H = im.size
    # A4 at 150 dpi
    check(f"page {i} is A4 at 150 dpi", (W, H) == (1240, 1753), f"{W}x{H}")
    bb = ImageOps.invert(im).getbbox()
    # the top accent bar spans the full width by design, so ignore the top strip
    sub = im.crop((0, 60, W, H))
    left = sum(sub.crop((0, 0, 40, H - 60)).histogram()[:200])
    right = sum(sub.crop((W - 40, 0, W, H - 60)).histogram()[:200])
    check(f"page {i} keeps out of the print margins", left == 0 and right == 0,
          f"left={left} right={right}")
    check(f"page {i} does not run past the bottom", bb[3] < H - 30, f"ink ends at {bb[3]} of {H}")

print("\n== the sheet is used, not half empty ==")
# the failure this catches: someone adds a block, the page silently grows, and
# the fix is 'shrink something' — or content shrinks and 40% of the sheet is
# blank, which looks unfinished on a wall
for i, png in enumerate(pages, 1):
    bands, _ = ink_bands(os.path.join(TMP, png))
    filled = sum(1 for b in bands[:9] if b > 0)
    check(f"page {i} has content in most of its height", filled >= 8, f"bands={bands}")

print("\n== every QR code decodes to the right address ==")
if not HAS_ZXING:
    print("  SKIP  zxing-cpp not installed — cannot verify QR contents")
else:
    p1 = qrs(os.path.join(TMP, "qms_install_guide_p1.png"))
    p2 = qrs(os.path.join(TMP, "qms_install_guide_p2.png"))
    check("page 1 has exactly one QR", len(p1) == 1, str(p1))
    check("   ... the counters address", p1 and p1[0].startswith("http://") and ":8099" in p1[0])
    check("page 2 has exactly two QRs", len(p2) == 2, str(p2))
    check("   ... the phone setup page", any("/setup/phone" in u for u in (p2 or [])))
    check("   ... the secure scanner address",
          any(u.startswith("https://") and ":8443" in u for u in (p2 or [])))

print("\n== the addresses are a LAN address, not localhost or a VPN ==")
# localhost on a printed sheet is useless: it means 'this device' on every device
for png in pages:
    q = qrs(os.path.join(TMP, png)) or []
    for u in q:
        check(f"   {u.split('//')[-1][:34]} is reachable from another device",
              "localhost" not in u and "127.0.0.1" not in u)

print("\n== the lines staff must paste are the real ones ==")
r = generate("--blank")
blank_p1 = qrs(os.path.join(TMP, "qms_install_guide_p1.png")) if HAS_ZXING else None
check("--blank still runs", r.returncode == 0)
check("--blank leaves the QR as a placeholder", blank_p1 and "192.168.0.0" in blank_p1[0],
      str(blank_p1))

# The one-liners have to match what actually exists, or the sheet teaches staff a
# command that fails. Cross-check the important ones against the repository.
for what, needle, where in [
    ("the install one-liner",
     "raw.githubusercontent.com/mozaiz/qms-so-fulfilment/main/install.sh", "README.md"),
    ("the restart one-liner", 'sh "$D/qms.sh" restart', "README.md"),
    ("the doctor one-liner", 'sh "$D/qms.sh" doctor', "README.md"),
]:
    in_guide = needle[:40] in SRC or needle.split("|")[-1][:52] in SRC
    check(f"{what} appears in the guide", in_guide)
    doc = open(os.path.join(REPO, where), encoding="utf-8").read()
    # detail only on failure: printing a failure message next to a passing check
    # is how a suite starts lying about what it verified
    check(f"   ... and matches {where}", needle in doc,
          "" if needle in doc else f"the guide has it, {where} does not — they have drifted apart")

print("\n== it says the things people only learn the hard way ==")
for phrase, why in [
    ("POS 1", "which machine this goes on — the single most important instruction"),
    ("SAME address", "P1-P4 share one address; only the role button differs"),
    ("Certificate Trust Settings", "the iOS switch everyone misses"),
    ("unsafely-treat-insecure-origin-as-secure", "the Android route that skips certificates"),
    ("stay switched on", "that POS 1 must stay on — it IS the server"),
    ("command not found", "the most likely failure when pasting a long line"),
]:
    check(f"it covers: {why}", phrase in SRC)

print("\n== the guide uses the running server's real addresses ==")
check("it asks QMS rather than guessing", "/api/network" in SRC)
check("   ... and still works with the server down", "netinfo" in SRC)

shutil.rmtree(TMP, ignore_errors=True)

print("\n" + "=" * 64)
print(f"PASSED {len(PASS)} / {len(PASS) + len(FAIL)}")
print("ALL GREEN" if not FAIL else "FAILURES: " + ", ".join(FAIL))
print("=" * 64)
sys.exit(0 if not FAIL else 1)
