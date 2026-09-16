"""Prove the phone-scanner path: a real CA, real TLS, and a camera-ready origin.

Scanning needs a camera. A camera needs a secure context. `http://192.168.x.x`
is not one, and on iOS there is no flag around it — Safari will not expose
`navigator.mediaDevices` on a page whose certificate it does not fully trust,
not even after tapping through the warning. So the only free, offline, no-purchase
way to scan with a phone camera is for the store to run its own tiny CA and for
the phone to genuinely trust it.

That is a lot of moving parts, and every one of them fails quietly: a cert that
misses the store's address, a profile iOS refuses, an HTTPS port that silently
falls back to plain HTTP. So each one is checked here.

    ./venv/bin/python deploy/test_https.py
"""

import json
import os
import plistlib
import sys as _sys
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO not in _sys.path:
    _sys.path.insert(0, REPO)
PY = sys.executable
HTTP_PORT = int(os.environ.get("QMS_TEST_HTTP_PORT", "8303"))
TLS_PORT = int(os.environ.get("QMS_TEST_TLS_PORT", "8444"))

PASS, FAIL = [], []
TMP = tempfile.mkdtemp(prefix="qms-https-")
CERT_DIR = os.path.join(TMP, "certs")


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  -- {detail}" if detail else ""))


def run(args, env=None, cwd=REPO, timeout=180):
    e = dict(os.environ, **(env or {}))
    return subprocess.run(args, env=e, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def ssl_ctx():
    ctx = ssl.create_default_context(cafile=os.path.join(CERT_DIR, "ca.crt"))
    return ctx


def lower_headers(h):
    """uvicorn sends lowercase header names; dict() preserves them verbatim, so a
    naive .get("Content-Type") silently returns None and the test lies."""
    return {k.lower(): v for k, v in dict(h).items()}


def get(url, ctx=None):
    try:
        r = urllib.request.urlopen(url, timeout=10, context=ctx)
        return r.status, r.read(), lower_headers(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), lower_headers(e.headers)
    except Exception as e:
        return None, str(e).encode(), {}


print("=" * 64)
print("The phone-scanner path: local CA, real TLS, camera-ready origin")
print("=" * 64)

# --------------------------------------------------------------- certificate
print("\n== make_cert.py ==")
r = run([PY, "make_cert.py"], env={"QMS_CERT_DIR": CERT_DIR})
check("it runs", r.returncode == 0, (r.stdout + r.stderr).strip().splitlines()[-1][:70])

ca_crt = os.path.join(CERT_DIR, "ca.crt")
srv_crt = os.path.join(CERT_DIR, "server.crt")
srv_key = os.path.join(CERT_DIR, "server.key")
ca_key = os.path.join(CERT_DIR, "ca.key")

check("it wrote a CA", os.path.exists(ca_crt))
check("it wrote a server certificate", os.path.exists(srv_crt))
check("it wrote the server key", os.path.exists(srv_key))

# The CA private key signs any certificate for anything. It stays on the store
# box and must never be readable by anyone else.
if os.path.exists(ca_key):
    mode = os.stat(ca_key).st_mode & 0o777
    check("the CA private key is 0600", mode == 0o600, oct(mode))
if os.path.exists(srv_key):
    mode = os.stat(srv_key).st_mode & 0o777
    check("the server private key is 0600", mode == 0o600, oct(mode))

# ------------------------------------------------------- covers every address
print("\n== the certificate names every address a phone might use ==")
import netinfo
want_names, want_ips = netinfo.all_names_and_ips()

out = run([PY, "-c", f"""
import sys
sys.path.insert(0, {REPO!r})
from cryptography import x509
c = x509.load_pem_x509_certificate(open({srv_crt!r},'rb').read())
san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
print('NAMES=' + ','.join(san.get_values_for_type(x509.DNSName)))
print('IPS=' + ','.join(str(i) for i in san.get_values_for_type(x509.IPAddress)))
print('DAYS=' + str((c.not_valid_after_utc - __import__('datetime').datetime.now(__import__('datetime').timezone.utc)).days))
"""], env={"QMS_CERT_DIR": CERT_DIR})
got_names, got_ips, days = set(), set(), 0
for line in out.stdout.splitlines():
    if line.startswith("NAMES="):
        got_names = set(line[6:].split(","))
    if line.startswith("IPS="):
        got_ips = set(filter(None, line[4:].split(",")))
    if line.startswith("DAYS="):
        days = int(line[5:])

# without the store's real address in the SAN the phone shows a certificate
# error and the camera never appears — and nothing in the UI would say why
check("every local address is in the SAN", want_ips <= got_ips,
      f"missing {sorted(want_ips - got_ips)}" if want_ips - got_ips else "")
check("localhost is in the SAN", "localhost" in got_names)
# Chrome rejects leaf certificates valid for more than 398 days
check("validity is under Chrome's 398-day limit", 0 < days < 398, f"{days} days")

# --------------------------------------------------------------- idempotence
print("\n== it does not churn the CA (phones have installed it) ==")
before = open(ca_crt).read()
r = run([PY, "make_cert.py"], env={"QMS_CERT_DIR": CERT_DIR})
check("a second run says there is nothing to do", "nothing to do" in r.stdout, r.stdout.strip().splitlines()[-1][:60])
check("the CA is untouched", open(ca_crt).read() == before)

# a store box on DHCP moves address; the certificate has to follow, or the
# camera stops working weeks later with no clue why
print("\n== it rebuilds when this machine's address changes ==")
r = run([PY, "-c", f"""
import sys
sys.path.insert(0, {REPO!r})
import make_cert, os
os.environ['QMS_CERT_DIR'] = {CERT_DIR!r}
make_cert.CERT_DIR = {CERT_DIR!r}
# pretend we moved to a new address
real = make_cert.local_addresses
make_cert.local_addresses = lambda: (real()[0], real()[1] | {{'10.9.9.9'}})
sys.argv = ['make_cert.py']
sys.exit(make_cert.main())
"""], env={"QMS_CERT_DIR": CERT_DIR})
check("it notices the new address and rebuilds", "10.9.9.9" in r.stdout and "refreshing" in r.stdout,
      r.stdout.strip().splitlines()[-1][:50] if r.stdout else r.stderr[-80:])
out = run([PY, "-c", f"""
import sys
sys.path.insert(0, {REPO!r})
from cryptography import x509
c = x509.load_pem_x509_certificate(open({srv_crt!r},'rb').read())
san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
print('OK' if '10.9.9.9' in [str(i) for i in san.get_values_for_type(x509.IPAddress)] else 'NO')
"""])
check("the new address is in the rebuilt certificate", "OK" in out.stdout)
check("the CA is STILL the same one (phones stay valid)",
      open(ca_crt).read() == before, "regenerating the CA would silently break every phone")

# ------------------------------------------------------------------ TLS serve
print("\n== HTTPS actually serves ==")
env = dict(os.environ, QMS_PORT=str(HTTP_PORT), QMS_TLS_PORT=str(TLS_PORT),
           QMS_DB=os.path.join(TMP, "qms.db"), QMS_STORE_CODE="TL01",
           QMS_STORE_NAME="TLS Test", QMS_CERT_DIR=CERT_DIR)
procs = []
try:
    procs.append(subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "0.0.0.0",
         "--port", str(TLS_PORT), "--ssl-keyfile", srv_key, "--ssl-certfile", srv_crt,
         "--log-level", "warning"], cwd=REPO, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    procs.append(subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "0.0.0.0",
         "--port", str(HTTP_PORT), "--log-level", "warning"], cwd=REPO, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

    lan_ip = (netinfo.local_ipv4() or ["127.0.0.1"])[0]
    up = False
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{HTTP_PORT}/api/health", timeout=2)
            up = True
            break
        except Exception:
            time.sleep(1)
    check("the servers came up", up)

    # trusted with the CA — exactly what the phone will do after installing it
    try:
        s = ssl_ctx()
        req = urllib.request.Request(f"https://{lan_ip}:{TLS_PORT}/api/health")
        body = json.loads(urllib.request.urlopen(req, timeout=10, context=s).read())
        check("HTTPS serves and the CA makes it trusted", body.get("ok") is True,
              f"store={body.get('store')}")
    except Exception as e:
        check("HTTPS serves and the CA makes it trusted", False, str(e)[:80])

    # and without the CA it must FAIL — a silently insecure scanner is worse
    # than no scanner, because nobody would ever investigate
    try:
        urllib.request.urlopen(f"https://{lan_ip}:{TLS_PORT}/api/health",
                               timeout=10, context=ssl.create_default_context())
        check("untrusted clients are REJECTED, not quietly allowed", False, "it served anyway")
    except ssl.SSLCertVerificationError:
        check("untrusted clients are REJECTED, not quietly allowed", True, "SSLCertVerificationError")
    except Exception as e:
        check("untrusted clients are REJECTED, not quietly allowed", True, type(e).__name__)

    # ------------------------------------------------------------------ routes
    print("\n== the phone-setup routes ==")
    st, body, hdrs = get(f"http://127.0.0.1:{HTTP_PORT}/api/network")
    net = json.loads(body) if st == 200 else {}
    check("/api/network advertises an https_url", bool(net.get("https_url")), net.get("https_url", ""))
    check("   ... on the tls port", str(TLS_PORT) in (net.get("https_url") or ""))
    check("   ... and reports tls_ready", net.get("tls_ready") is True)

    st, body, hdrs = get(f"http://127.0.0.1:{HTTP_PORT}/setup/ca.crt")
    check("/setup/ca.crt serves the CA over PLAIN http (where the phone starts)", st == 200, f"status={st}")
    check("   ... as a certificate type iOS/Android will install",
          "x-x509-ca-cert" in (hdrs.get("content-type") or ""), hdrs.get("content-type", ""))
    check("   ... and it is the real CA", b"BEGIN CERTIFICATE" in body)

    st, body, hdrs = get(f"http://127.0.0.1:{HTTP_PORT}/setup/ca.mobileconfig")
    check("/setup/ca.mobileconfig serves", st == 200, f"status={st} {body[:60]}")
    check("   ... with the iOS content type",
          "apple-aspen-config" in (hdrs.get("content-type") or ""), hdrs.get("content-type", ""))
    if st == 200:
        prof = plistlib.loads(body)
        check("   ... as a valid iOS configuration profile", prof.get("PayloadType") == "Configuration")
        inner = (prof.get("PayloadContent") or [{}])[0]
        check("   ... carrying a ROOT certificate payload",
              inner.get("PayloadType") == "com.apple.security.root", inner.get("PayloadType"))
        check("   ... with real DER inside", len(inner.get("PayloadContent") or b"") > 400,
              f"{len(inner.get('PayloadContent') or b'')} bytes")

    st, body, hdrs = get(f"http://127.0.0.1:{HTTP_PORT}/setup/phone")
    page = body.decode(errors="replace")
    check("/setup/phone serves", st == 200, f"status={st}")
    # iOS needs a SECOND step that people skip; the page must insist on it
    check("   ... and insists on Certificate Trust Settings (iOS Full Trust)",
          "Certificate Trust Settings" in page)
    check("   ... and explains why localhost will not work on a phone",
          "localhost" in page and "this phone" in page.lower())
    check("   ... and gives the Android chrome://flags route",
          "unsafely-treat-insecure-origin-as-secure" in page)
    # iOS installs a profile; Android cannot open one and needs the bare cert.
    # Two buttons, two different files — and getting this wrong is invisible
    # until a store tries it.
    check("   ... and sends iOS to the profile, Android to the bare certificate",
          'os === "ios" ? "/setup/ca.mobileconfig" : "/setup/ca.crt"' in page)
    check("   ... and never caches a certificate download",
          (hdrs.get("cache-control") or "").startswith("no-store"))

    st, body, hdrs = get(f"http://127.0.0.1:{HTTP_PORT}/setup/ca.crt")
    st2, body2, hdrs2 = get(f"https://{lan_ip}:{TLS_PORT}/setup/ca.crt", ssl_ctx())
    check("the CA is reachable over https too", st2 == 200, f"status={st2}")

finally:
    for p in procs:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    shutil.rmtree(TMP, ignore_errors=True)

print("\n" + "=" * 64)
print(f"PASSED {len(PASS)} / {len(PASS) + len(FAIL)}")
print("ALL GREEN" if not FAIL else "FAILURES: " + ", ".join(FAIL))
print("=" * 64)
sys.exit(0 if not FAIL else 1)
