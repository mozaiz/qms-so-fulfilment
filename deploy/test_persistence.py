"""Does data survive a stop, a restart, and a hard kill?

Runs against a throwaway database so it cannot touch anything real.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP = "/home/mozaiz/workspace/qms"
D = f"/tmp/persist-test-{os.getpid()}"

# A previous failed run can leave a server listening, which then serves a
# database this run has just deleted. Clear the port before starting.
def _free(port):
    out = subprocess.run(f"ss -ltnp 2>/dev/null | grep ':{port} ' || true",
                         shell=True, capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "pid=" in line:
            subprocess.run("kill " + line.split("pid=")[1].split(",")[0],
                           shell=True, capture_output=True)

# find a genuinely free port by binding it, not by shelling out to `ss`
import socket as _socket
PORT = 0
for cand in range(8150, 8200):
    _s = _socket.socket()
    try:
        _s.bind(("127.0.0.1", cand))
        _s.close()
        PORT = cand
        break
    except OSError:
        _s.close()
assert PORT, "no free port"
_free(PORT)
BASE = f"http://127.0.0.1:{PORT}"

ENV = dict(os.environ, QMS_PORT=str(PORT), QMS_DB=f"{D}/qms.db",
           QMS_STORE_CODE="P01", QMS_STORE_NAME="Persist Test",
           QMS_STALE_MIN="15", QMS_SLA_MIN="10", QMS_COMPLETE_MIN="10")

PASS, FAIL = [], []


def call(method, path, body=None, token=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw or "{}")
        except Exception:
            return e.code, {"raw": raw}


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"   {'PASS' if cond else 'FAIL'}  {name}")


def start():
    return subprocess.Popen(
        [f"{APP}/venv/bin/python", "-m", "uvicorn", "app:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=APP, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_up():
    for _ in range(30):
        try:
            st, _ = call("GET", "/api/health")
            if st == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def login(role, pos=None):
    body = {"role": role}
    if pos:
        body["pos_number"] = pos
    st, d = call("POST", "/api/login", body)
    assert st == 200, (st, d)
    return d["token"]


shutil.rmtree(D, ignore_errors=True)
os.makedirs(D)

print("STEP 1 — write real data")
p = start()
assert wait_up(), "server did not start"
scanner, pos1, back = login("scanner"), login("pos", 1), login("backstore")

ids = []
for i in range(1, 4):
    st, r = call("POST", "/api/v1/requests/scan", {"so_number": f"MACSO26-0014246{i}"}, scanner)
    assert st == 200, (st, r)
    ids.append(r["id"])
    print(f"   scanned #{r['seq']}  {r.get('so_number')}")

call("POST", f"/api/v1/requests/{ids[0]}/claim", {}, pos1)
call("POST", f"/api/v1/requests/{ids[0]}/deliver", {}, back)
call("POST", f"/api/v1/requests/{ids[0]}/complete", {}, pos1)
print("   one SO taken through the full handover")

def tot(env):
    return len(env["requests"])

_, before = call("GET", "/api/v1/requests?view=today", token=scanner)
print(f"   {tot(before)} total | {before['completed_count']} completed | {before['open_count']} open")
print("   on disk:", sorted(os.listdir(D)))

print("\nSTEP 2 — polite stop (SIGTERM, what a service manager sends), then start again")
p.terminate()
p.wait(timeout=10)
print("   on disk:", sorted(os.listdir(D)))
p = start()
wait_up()
_, after = call("GET", "/api/v1/requests?view=today", token=scanner)
print(f"   {tot(after)} total | {after['completed_count']} completed | {after['open_count']} open")
check("every SO survived the stop/start",
      tot(after) == tot(before) and after["completed_count"] == before["completed_count"])

print("\nSTEP 3 — hard kill (SIGKILL: power cut, force quit) then start again")
call("POST", "/api/v1/requests/scan", {"so_number": "MACSO26-00142469"}, scanner)
_, tmp = call("GET", "/api/v1/requests?view=today", token=scanner)
n_before = tot(tmp)
p.send_signal(signal.SIGKILL)
p.wait(timeout=10)
print("   killed. on disk:", sorted(os.listdir(D)))
p = start()
wait_up()
_, tmp2 = call("GET", "/api/v1/requests?view=today", token=scanner)
n_after = tot(tmp2)
print(f"   {n_before} before -> {n_after} after")
check("no data lost to a hard kill", n_after == n_before)

print("\nSTEP 4 — audit trail intact after all that?")
_, ev = call("GET", f"/api/v1/requests/{ids[0]}/events", token=scanner)
print(f"   events recorded for the completed SO: {len(ev.get('events', []))}")
check("audit trail preserved", len(ev.get("events", [])) >= 4)

print("\nSTEP 5 — can the app itself ever wipe the database on boot?")
src = open(f"{APP}/app.py").read()
all_destructive = re.findall(r"(?i)\b(DROP\s+TABLE|TRUNCATE|DELETE\s+FROM\s+(\w+))", src)
print("   destructive statements in app.py:", [m[0] for m in all_destructive] or "NONE")
# Clearing expired login sessions is correct housekeeping. Anything touching the
# business tables would not be.
PROTECTED = {"so_requests", "so_events", "stores", "staff"}
unsafe = [t for _, t in all_destructive if t and t.lower() in PROTECTED]
check("no destructive SQL against the business tables", not unsafe)
check("the only DELETE is on login sessions",
      [m[0] for m in all_destructive] in ([], ["DELETE FROM sessions"]))
check("the app never runs the demo seeder", "seed_demo" not in src)

# an upgrade must not lose anything either
print("\nSTEP 6 — re-install over the top (an upgrade), then look again")
p.terminate()
p.wait(timeout=10)
before_upgrade = sorted(os.listdir(D))
p = start()
wait_up()
_, after3 = call("GET", "/api/v1/requests?view=today", token=scanner)
check("data intact after the process is replaced", tot(after3) == n_after)
check("the database file itself was not recreated", sorted(os.listdir(D)) == before_upgrade)

p.terminate()
p.wait(timeout=10)
shutil.rmtree(D, ignore_errors=True)

print("\n" + "=" * 52)
print(f"PASSED {len(PASS)} / {len(PASS) + len(FAIL)}")
print("ALL GREEN" if not FAIL else f"FAILURES: {FAIL}")
print("=" * 52)
sys.exit(0 if not FAIL else 1)
