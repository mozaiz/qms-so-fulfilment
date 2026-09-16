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


def free_port():
    """Ask the OS for an unused port.

    Hard-coding a test port is how a suite passes alone and fails in a batch: the
    run where something else holds it fails four sections later, as a bare
    status=None that says nothing about the real cause.
    """
    import socket as _s
    s = _s.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


HTTP_PORT = int(os.environ.get("QMS_TEST_HTTP_PORT") or free_port())
TLS_PORT = int(os.environ.get("QMS_TEST_TLS_PORT") or free_port())

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
    # Keep their output. A server that refuses to start is the failure this suite
    # exists to catch, and swallowing its stderr turns that into a mystery.
    tls_log = open(os.path.join(TMP, "tls-server.log"), "w+")
    http_log = open(os.path.join(TMP, "http-server.log"), "w+")
    procs.append(subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "0.0.0.0",
         "--port", str(TLS_PORT), "--ssl-keyfile", srv_key, "--ssl-certfile", srv_crt,
         "--log-level", "warning"], cwd=REPO, env=env, stdout=tls_log, stderr=tls_log))
    procs.append(subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "0.0.0.0",
         "--port", str(HTTP_PORT), "--log-level", "warning"], cwd=REPO, env=env,
        stdout=http_log, stderr=http_log))

    lan_ip = (netinfo.local_ipv4() or ["127.0.0.1"])[0]
    def wait_for(url, ctx=None, tries=40):
        for _ in range(tries):
            try:
                urllib.request.urlopen(url, timeout=2, context=ctx)
                return True
            except Exception:
                time.sleep(1)
        return False

    up = wait_for(f"http://127.0.0.1:{HTTP_PORT}/api/health")
    check("the plain server came up", up)

    # The TLS listener is the entire subject of this suite, so prove it answered
    # rather than inferring it from the plain one. Without its own readiness check
    # a TLS start failure shows up later as an unrelated-looking status=None.
    tls_up = wait_for(f"https://127.0.0.1:{TLS_PORT}/api/health", ssl_ctx(), tries=25)
    check("the secure server came up", tls_up,
          "" if tls_up else "it never answered — see the server output below")

    if not (up and tls_up):
        for name, fh in (("secure", tls_log), ("plain", http_log)):
            fh.flush()
            fh.seek(0)
            body = fh.read().strip()
            if body:
                print(f"  ---- {name} server output ----")
                for line in body.splitlines()[-15:]:
                    print("    " + line)

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

    print("\n== whose certificate it is ==")
    # The Organization field is what a phone shows when someone opens the
    # certificate details, and what the iOS install screen displays. It is the
    # only part of this that a person ever reads.
    org = run([PY, "-c", f"""
import sys
sys.path.insert(0, {REPO!r})
from cryptography import x509
from cryptography.x509.oid import NameOID
for f in ("ca.crt", "server.crt"):
    c = x509.load_pem_x509_certificate(open({CERT_DIR!r} + "/" + f, "rb").read())
    o = c.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
    cn = c.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    print(f + "|" + (o[0].value if o else "") + "|" + (cn[0].value if cn else ""))
"""], env={"QMS_CERT_DIR": CERT_DIR})
    rows = dict()
    for line in org.stdout.splitlines():
        if "|" in line:
            f, o, cn = line.split("|")
            rows[f] = (o, cn)
    check("the CA names its owner", rows.get("ca.crt", ("", ""))[0] == "Zairi Khaidzir @ Mozaiz",
          str(rows.get("ca.crt")))
    check("the server certificate names its owner too",
          rows.get("server.crt", ("", ""))[0] == "Zairi Khaidzir @ Mozaiz",
          str(rows.get("server.crt")))
    check("   ... and the server cert still names the machine, not the person",
          rows.get("server.crt", ("", ""))[1] not in ("", "Zairi Khaidzir @ Mozaiz"),
          rows.get("server.crt", ("", ""))[1])

    st, body, _ = get(f"http://127.0.0.1:{HTTP_PORT}/setup/ca.mobileconfig")
    prof = plistlib.loads(body)
    check("the iOS profile shows the owner before you trust it",
          prof.get("PayloadOrganization") == "Zairi Khaidzir @ Mozaiz",
          str(prof.get("PayloadOrganization")))
    check("   ... and the profile name carries it",
          "Zairi Khaidzir @ Mozaiz" in (prof.get("PayloadDisplayName") or ""),
          prof.get("PayloadDisplayName", ""))
    # The printed guide tells staff to look for this EXACT label in Certificate
    # Trust Settings. Renaming it breaks the sheet on the wall.
    inner = (prof.get("PayloadContent") or [{}])[0]
    check("   ... but the cert label the guide names is UNCHANGED",
          inner.get("PayloadDisplayName") == "QMS Store Certificate Authority",
          str(inner.get("PayloadDisplayName")))

    # A certificate's subject cannot be edited, so a changed name is a changed
    # CA — and that silently un-trusts every phone already set up.
    print("\n== changing the name rebuilds the CA and says so ==")
    moved = run([PY, "make_cert.py"], env={"QMS_CERT_DIR": CERT_DIR, "QMS_CERT_ORG": "Someone Else"})
    check("a different name is treated as a different CA",
          "DIFFERENT certificate authority" in moved.stdout, moved.stdout.strip().splitlines()[-1][:70])
    check("   ... and it warns that phones must be set up again",
          "must install the new one" in moved.stdout)
    # Changing the name back is itself another change, so this run rebuilds too.
    back = run([PY, "make_cert.py"], env={"QMS_CERT_DIR": CERT_DIR})
    check("   ... and changing it back is also treated as a change",
          "DIFFERENT certificate authority" in back.stdout)
    steady = run([PY, "make_cert.py"], env={"QMS_CERT_DIR": CERT_DIR})
    check("   ... and once the name matches it settles down",
          "nothing to do" in steady.stdout, steady.stdout.strip().splitlines()[-1][:60])
    check("   ... reporting whose it is", "Zairi Khaidzir @ Mozaiz" in steady.stdout)

    # Staff type this from memory, on a phone, in a hurry.
    print("\n== near-miss addresses go to the page, not to a JSON 404 ==")
    for typo in ("/phone/setup", "/phone", "/phonesetup", "/setup"):
        req = urllib.request.Request(f"http://127.0.0.1:{HTTP_PORT}{typo}")
        try:
            r2 = urllib.request.urlopen(req, timeout=8)
            final = r2.geturl()
            check(f"{typo} lands on the setup page", final.endswith("/setup/phone"), final)
        except Exception as e:
            check(f"{typo} lands on the setup page", False, type(e).__name__)

    # A stale certificate fails on the phone and nowhere else. The app has to say
    # so, on both the screen where the fix lives and the page the phone opens.
    print("\n== a certificate that no longer matches this machine is reported ==")
    st, body, _ = get(f"http://127.0.0.1:{HTTP_PORT}/api/network")
    net2 = json.loads(body)
    check("/api/network reports the certificate state", "tls_cert" in net2, str(net2.get("tls_cert"))[:60])
    check("   ... and it is healthy while the address is unchanged",
          (net2.get("tls_cert") or {}).get("ok") is True)

    stale = run([PY, "-c", f"""
import sys, os
sys.path.insert(0, {REPO!r})
os.environ['QMS_CERT_DIR'] = {CERT_DIR!r}
import netinfo, app
app.CERT_DIR = {CERT_DIR!r}
real = netinfo.local_ipv4
netinfo.local_ipv4 = lambda: real() + ['192.0.2.77']   # a documentation address, never a real one: a test must not
    # encode the author's own network
app._lan_addresses = netinfo.local_ipv4
print(app._cert_covers_current())
"""])
    check("   ... and it DETECTS a moved address", "'ok': False" in stale.stdout, stale.stdout.strip()[:90])
    check("   ... naming the address it lacks", "192.0.2.77" in stale.stdout)

    st, body, _ = get(f"http://127.0.0.1:{HTTP_PORT}/setup/phone")
    page2 = body.decode(errors="replace")
    check("the phone page can show the stale-certificate warning",
          "certificate is out of date" in page2.lower())

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
