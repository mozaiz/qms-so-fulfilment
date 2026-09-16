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
import tempfile
import time
import urllib.error
import urllib.request

# Repo root, derived rather than hardcoded: this runs in CI on a GitHub runner
# where no such path exists.
APP = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
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
    # sys.executable, not APP/venv/bin/python: CI installs the dependencies into
    # the runner's interpreter and there is no venv/ in the checkout. Running
    # with the same interpreter that runs this test is correct everywhere.
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app",
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

# ---------------------------------------------------------------- startup race
print("\n== four listeners can start on the same fresh database at once ==")
# run.sh starts the plain and the secure listener, and on a first install both
# create, migrate and seed the same database. That is a real race on a fresh
# install and it used to kill one of them, leaving a store where either the
# counters or the scanner simply does not answer.
#
# PROCESSES, not threads. An earlier version of this check used threads, which
# pass however badly the app is broken — the GIL serialises the SQLite calls, so
# the interleaving that actually loses the race never happens. It reported five
# clean trials against code that fails in CI every time.
race_db = os.path.join(tempfile.gettempdir(), "qms-race.db")
race_env = dict(os.environ, QMS_DB=race_db, QMS_STORE_CODE="RACE",
                QMS_STORE_NAME="Race Test")

BOOT = ("import os, sys; sys.path.insert(0, os.getcwd()); "
        "import app; app.init_db()")

trials_clean = 0
TRIALS, WORKERS = 5, 4
for trial in range(TRIALS):
    for suffix in ("", "-wal", "-shm"):
        os.path.exists(race_db + suffix) and os.remove(race_db + suffix)
    # start them as close together as possible
    procs = [subprocess.Popen([sys.executable, "-c", BOOT], cwd=APP, env=race_env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for _ in range(WORKERS)]
    outs = [p.communicate(timeout=90) for p in procs]
    bad = [(p.returncode, (o + e).decode(errors="replace").strip().splitlines()[-1:])
           for p, (o, e) in zip(procs, outs) if p.returncode != 0]
    if bad:
        print(f"        trial {trial + 1}: FAILED — {bad[0][0]} rc, {bad[0][1]}")
    else:
        trials_clean += 1
        print(f"        trial {trial + 1}: all {WORKERS} exited cleanly")
for suffix in ("", "-wal", "-shm"):
    os.path.exists(race_db + suffix) and os.remove(race_db + suffix)
check(f"all {TRIALS} rounds of {WORKERS} simultaneous boots succeeded",
      trials_clean == TRIALS)

print("\n" + "=" * 52)
print(f"PASSED {len(PASS)} / {len(PASS) + len(FAIL)}")
print("ALL GREEN" if not FAIL else f"FAILURES: {FAIL}")
print("=" * 52)
sys.exit(0 if not FAIL else 1)
