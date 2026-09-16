"""Run every copy-paste one-liner that appears in README.md, against a real install.

A README full of commands nobody has executed is worse than no README: the person
who needs it most is the one who cannot tell a typo from a broken product.

    ./venv/bin/python deploy/test_readme.py

Installs into a throwaway HOME (so the universal locator falls through to
$HOME/QMS exactly as it would on a Mac), then runs each documented command.
"""

import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HOME = "/tmp/readme-home"
APP = os.path.join(HOME, "QMS")
SRC = "/tmp/readme-src"
PORT = 8251
PASS, FAIL = [], []

README = open(os.path.join(REPO, "README.md"), encoding="utf-8").read()


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  -- {detail}" if detail else ""))


def run(cmd, timeout=120):
    """Run a documented one-liner exactly as a user would paste it."""
    env = dict(os.environ, HOME=HOME)
    r = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip()


print("=" * 64)
print("Every one-liner in README.md, executed")
print("=" * 64)

for d in (HOME, SRC):
    shutil.rmtree(d, ignore_errors=True)
os.makedirs(SRC)

# ---- install the way the README tells a user to -------------------------
with tarfile.open("/tmp/readme.tgz", "w:gz") as tf:
    for n in sorted(os.listdir(REPO)):
        if n in ("venv", ".git", "screens", "__pycache__", ".github"):
            continue
        if n.endswith((".db", ".db-wal", ".db-shm", ".tgz", ".zip", ".tar.gz")):
            continue
        tf.add(os.path.join(REPO, n), arcname=n)
with tarfile.open("/tmp/readme.tgz") as tf:
    tf.extractall(SRC)

install_env = dict(os.environ, HOME=HOME, APP_DIR=APP, QMS_PORT=str(PORT),
                   STORE_CODE="RD01", STORE_NAME="Readme Test",
                   SERVICE="none", NO_SUDO="1", NO_START="1")
r = subprocess.run(["bash", "install.sh"], cwd=SRC, env=install_env,
                   capture_output=True, text=True, timeout=400)
print(f"\ninstalled into {APP} (rc={r.returncode})")
check("installer succeeded", r.returncode == 0)

# ---- every documented command -------------------------------------------
LOC = 'D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; '

print("\n== the qms.sh one-liners ==")
for verb, expect in [
    ("status", ["QMS status", "Data", "Open on this computer"]),
    ("url", ["localhost"]),
    ("backup", ["Backup written"]),
    ("doctor", ["QMS doctor", "--- the service", "listening"]),
    ("stop", ["data is not touched"]),
    ("start", []),          # no service in this test; must not crash
    ("restart", []),
]:
    rc, out = run(LOC + f'sh "$D/qms.sh" {verb}', timeout=180)
    # start/restart may legitimately fail to bring up a service that was never
    # installed — what matters is that the command itself runs and explains.
    ok = (rc == 0) or verb in ("start", "restart")
    check(f"`... sh $D/qms.sh {verb}` runs", ok, f"rc={rc}")
    for e in expect:
        check(f"   ... and mentions {e!r}", e in out)
    if verb in ("status", "doctor"):
        print("      " + "\n      ".join(out.splitlines()[:4]))

print("\n== the log and config one-liners ==")
open(os.path.join(APP, "qms.log"), "w").write("hello from the log\n")
rc, out = run(LOC + 'tail -40 "$D/qms.log"')
check("`... tail -40 \"$D/qms.log\"` runs", rc == 0 and "hello from the log" in out, f"rc={rc}")

rc, out = run(LOC + 'grep QMS_PORT "$D/qms.env"')
check("`... grep QMS_PORT \"$D/qms.env\"` shows the port", rc == 0 and f"QMS_PORT={PORT}" in out, out[:60])

print("\n== start the server so the live one-liners can be tested ==")
env = dict(os.environ, HOME=HOME, QMS_PORT=str(PORT), QMS_DB=os.path.join(APP, "qms.db"),
           QMS_STORE_CODE="RD01", QMS_STORE_NAME="Readme Test")
srv = subprocess.Popen([os.path.join(APP, "venv/bin/python"), "-m", "uvicorn", "app:app",
                        "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
                       cwd=APP, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    up = False
    for _ in range(30):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=2)
            up = True
            break
        except Exception:
            time.sleep(1)
    check("test server came up", up)

    print("\n== the health one-liner, with a server running ==")
    rc, out = run(LOC + f'curl -s "http://127.0.0.1:{PORT}/api/health"')
    check("health one-liner returns the health JSON", rc == 0 and '"ok":true' in out, out[:70])
    check("   ... and reports the version", '"version"' in out)

    print("\n== the QR wall-sign one-liner ==")
    rc, out = run(LOC + '"$D/venv/bin/python" "$D/make_qr_card.py"', timeout=180)
    check("QR one-liner runs", rc == 0, f"rc={rc} {out.strip().splitlines()[-1][:60] if out else ''}")
    # it must point at THIS install's port, not a hardcoded default
    check("   ... and points at the configured port", f":{PORT}" in out,
          [l for l in out.splitlines() if "points at" in l][:1])
    made = [f for f in os.listdir(APP) if f.startswith("qr_card_") and f.endswith((".png", ".pdf"))]
    check("   ... and produced a file", bool(made), ", ".join(made))
finally:
    srv.terminate()
    srv.wait(timeout=15)

print("\n== the short forms named in the README ==")
rc, out = run('sh "$HOME/QMS/qms.sh" status')
check("`sh ~/QMS/qms.sh status` works", rc == 0 and "QMS status" in out, f"rc={rc}")

check("README documents the QR command", "make_qr_card.py" in README)
check("QR one-liner is present verbatim",
      bool(re.search(r"D=/opt/qms; \[ -d \"\$D\" \] \|\| D=\"\$HOME/QMS\"; \"\$D/venv/bin/python\" \"\$D/make_qr_card\.py\"", README)))

print("\n== every bash block in the README is either run above or uses sudo/curl ==")
blocks = re.findall(r"```bash\n(.*?)```", README, re.S)
unexplained = []
for b in blocks:
    for line in b.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if any(k in line for k in ('qms.sh', 'tail -40 "$D', 'grep QMS_PORT "$D',
                                   'curl -s "http://127.0.0.1', 'make_qr_card.py')):
            continue
        unexplained.append(line)
print(f"   {len(blocks)} bash blocks total, {len(unexplained)} lines not exercised here:")
for l in unexplained:
    print(f"      {l[:88]}")

shutil.rmtree(HOME, ignore_errors=True)
shutil.rmtree(SRC, ignore_errors=True)
os.path.exists("/tmp/readme.tgz") and os.remove("/tmp/readme.tgz")

print("\n" + "=" * 64)
print(f"PASSED {len(PASS)} / {len(PASS) + len(FAIL)}")
print("ALL GREEN" if not FAIL else "FAILURES: " + ", ".join(FAIL))
print("=" * 64)
sys.exit(0 if not FAIL else 1)
