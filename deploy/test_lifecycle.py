"""Lifecycle test: install -> uninstall -> re-install -> purge.

The promise being tested is the one that matters to a store: removing QMS must
NOT remove the day's queue history, and putting it back must find that history
exactly where it was left.

    ./venv/bin/python deploy/test_lifecycle.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = "/tmp/lc-src"
APP = "/tmp/lc-app"
SCRATCH = "/tmp/lc-scratch"

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")


def sh(args, cwd=None, env=None, timeout=280):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)


def free_port():
    """Bind-test rather than shelling out to `ss`, which is not everywhere."""
    import socket
    for cand in range(8300, 8380):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", cand))
            s.close()
            return cand
        except OSError:
            s.close()
    raise RuntimeError("no free port")


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"


def call(method, path, body=None, token=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + token} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw or "{}")
        except Exception:
            return e.code, {"raw": raw}


def boot():
    env = dict(os.environ, QMS_PORT=str(PORT), QMS_DB=f"{APP}/qms.db",
               QMS_STORE_CODE="LC01", QMS_STORE_NAME="Lifecycle Test")
    p = subprocess.Popen([f"{APP}/venv/bin/python", "-m", "uvicorn", "app:app",
                          "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
                         cwd=APP, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        try:
            if call("GET", "/api/health")[0] == 200:
                return p
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError("did not come up")


def install_env(**extra):
    e = {"PATH": os.environ["PATH"], "HOME": SCRATCH,
         "APP_DIR": APP, "QMS_PORT": str(PORT), "STORE_CODE": "LC01",
         "STORE_NAME": "Lifecycle Test", "SERVICE": "none", "NO_SUDO": "1",
         "NO_START": "1"}
    e.update(extra)
    return e


print("=" * 60)
print("QMS lifecycle: install / uninstall / re-install / purge")
print("=" * 60)

for d in (SRC, APP, SCRATCH):
    shutil.rmtree(d, ignore_errors=True)
os.makedirs(SCRATCH)

print("\n== set up: extract the repo and install ==")
with tarfile.open("/tmp/lc.tar.gz", "w:gz") as tf:
    for name in sorted(os.listdir(REPO)):
        if name in ("venv", ".git", "screens", "__pycache__", ".github"):
            continue
        if name.endswith((".db", ".db-wal", ".db-shm", ".tgz", ".zip", ".tar.gz")):
            continue
        tf.add(os.path.join(REPO, name), arcname=name)
os.makedirs(SRC)
with tarfile.open("/tmp/lc.tar.gz") as tf:
    tf.extractall(SRC)

r = sh(["bash", "install.sh"], cwd=SRC, env=install_env())
check("installer exits 0", r.returncode == 0)
for f in ("app.py", "qms.env", "qms.sh", "qms-backup.sh"):
    check(f"installed: {f}", os.path.exists(os.path.join(APP, f)))

print("\n== put real data in ==")
p = boot()
_, tok = call("POST", "/api/login", {"role": "scanner"})
_, ptok = call("POST", "/api/login", {"role": "pos", "pos_number": 1})
_, btok = call("POST", "/api/login", {"role": "backstore"})
tok, ptok, btok = tok["token"], ptok["token"], btok["token"]
first = None
for i in range(1, 4):
    _, r0 = call("POST", "/api/v1/requests/scan", {"so_number": f"MACSO26-0014246{i}"}, tok)
    if first is None:
        first = r0["id"]
call("POST", f"/api/v1/requests/{first}/claim", {}, ptok)
call("POST", f"/api/v1/requests/{first}/deliver", {}, btok)
call("POST", f"/api/v1/requests/{first}/complete", {}, ptok)
_, env0 = call("GET", "/api/v1/requests?view=today", token=tok)
n0 = len(env0["requests"])
print(f"   {n0} SOs written, {env0['completed_count']} completed")
p.terminate()
p.wait(timeout=15)
subprocess.run(["sh", os.path.join(APP, "qms-backup.sh")], capture_output=True, timeout=60)

print("\n== --status ==")
r = sh(["bash", "install.sh", "--status"], cwd=SRC, env=install_env())
check("--status exits 0", r.returncode == 0)
check("--status finds the data", "so_requests" not in r.stdout and "SOs in total" in r.stdout)
check("--status names the database", "qms.db" in r.stdout)
check("--status reports the backup", "Last backup" in r.stdout)
print("\n".join("   " + l for l in r.stdout.strip().splitlines()))

print("\n== --uninstall (the default: remove the program, keep the data) ==")
r = sh(["bash", "install.sh", "--uninstall"], cwd=SRC, env=install_env())
check("--uninstall exits 0", r.returncode == 0)
check("program removed  (app.py)", not os.path.exists(f"{APP}/app.py"))
check("program removed  (venv)", not os.path.exists(f"{APP}/venv"))
check("program removed  (static)", not os.path.exists(f"{APP}/static"))
check("DATA KEPT        (qms.db)", os.path.exists(f"{APP}/qms.db"))
check("DATA KEPT        (qms.env)", os.path.exists(f"{APP}/qms.env"))
check("DATA KEPT        (backups/)", os.path.isdir(f"{APP}/backups"))
check("says the data was kept on purpose", "kept on purpose" in r.stdout)

print("\n== re-install over the top ==")
# Customise the settings the way an operator would, so the upgrade has something
# real to lose. Checking that qms.env merely EXISTS is what let a rsync --delete
# wipe every outlet's configuration on upgrade while the suite stayed green.
cfg = open(f"{APP}/qms.env").read()
cfg = cfg.replace("QMS_STALE_MIN=15", "QMS_STALE_MIN=25")
# regex, not a literal: the value depends on what the installer was given
cfg = re.sub(r'^QMS_STORE_CODE=.*$', 'QMS_STORE_CODE="KEEP01"', cfg, flags=re.M)
assert "KEEP01" in cfg, "failed to customise the store code before the upgrade"
open(f"{APP}/qms.env", "w").write(cfg)
open(f"{APP}/qms.log", "w").write("operator log line\n")

r = sh(["bash", "install.sh"], cwd=SRC, env=install_env())
check("re-install exits 0", r.returncode == 0)
check("app.py is back", os.path.exists(f"{APP}/app.py"))

after = open(f"{APP}/qms.env").read()
check("upgrade KEEPS a customised threshold", "QMS_STALE_MIN=25" in after)
check("upgrade KEEPS a customised store code", 'QMS_STORE_CODE="KEEP01"' in after)
check("upgrade keeps the log", os.path.exists(f"{APP}/qms.log"))
check("upgrade keeps backups/", os.path.isdir(f"{APP}/backups"))
p = boot()
_, tok2 = call("POST", "/api/login", {"role": "scanner"})
_, env1 = call("GET", "/api/v1/requests?view=today", token=tok2["token"])
n1 = len(env1["requests"])
print(f"   after re-install: {n1} SOs, {env1['completed_count']} completed  (was {n0}, {env0['completed_count']})")
check("THE DATA CAME BACK INTACT", n1 == n0 and env1["completed_count"] == env0["completed_count"])
p.terminate()
p.wait(timeout=15)

print("\n== safety: --purge on its own must refuse ==")
r = sh(["bash", "install.sh", "--purge"], cwd=SRC, env=install_env())
check("bare --purge is refused", r.returncode != 0)
check("and says why", "only means something together with --uninstall" in (r.stdout + r.stderr))
check("nothing was deleted", os.path.exists(f"{APP}/qms.db"))

print("\n== --uninstall --purge ==")
r = sh(["bash", "install.sh", "--uninstall", "--purge"], cwd=SRC, env=install_env())
check("purge exits 0", r.returncode == 0)
check("everything gone", not os.path.exists(APP))

print("\n== --help ==")
r = sh(["bash", "install.sh", "--help"], cwd=SRC, env=install_env())
check("--help exits 0", r.returncode == 0)
check("--help documents the modes", "--uninstall" in r.stdout and "--status" in r.stdout)

print("\n== qms.sh helper ==")
shutil.rmtree(APP, ignore_errors=True)
sh(["bash", "install.sh"], cwd=SRC, env=install_env())
check("qms.sh is executable", os.access(f"{APP}/qms.sh", os.X_OK))
r = sh(["sh", f"{APP}/qms.sh", "url"], cwd=APP, env=install_env())
check("qms.sh url prints an address", "http://localhost:" in r.stdout)
r = sh(["sh", f"{APP}/qms.sh", "stop"], cwd=APP, env=install_env())
check("qms.sh stop works and reassures about data", "data is not touched" in r.stdout)
r = sh(["sh", f"{APP}/qms.sh", "nonsense"], cwd=APP, env=install_env())
check("qms.sh rejects an unknown command", r.returncode != 0)

print("\n== qms.sh doctor — the thing you run when nothing works ==")
r = sh(["sh", f"{APP}/qms.sh", "doctor"], cwd=APP, env=install_env())
check("doctor exits 0", r.returncode == 0)
check("doctor names the port", "port" in r.stdout.lower())
check("doctor checks the files", "app.py" in r.stdout and "venv" in r.stdout)
for want in ("--- the service", "listening", "does it answer", "the log"):
    check(f"doctor section: {want}", want in r.stdout)
check("doctor reports nothing listening while stopped", "NOTHING is listening" in r.stdout)
check("doctor says there is no log yet", "no qms.log" in r.stdout)

# and with the server actually up, doctor must notice
p = boot()
r = sh(["sh", f"{APP}/qms.sh", "doctor"], cwd=APP, env=install_env())
check("doctor sees it answering when up", "yes:" in r.stdout)
check("doctor sees the database", "ok       qms.db" in r.stdout)
p.terminate()
p.wait(timeout=15)

print("\n== safety: a failed start must diagnose itself, not lie ==")
r = sh(["sh", f"{APP}/qms.sh", "start"], cwd=APP, env=install_env(), timeout=120)
check("a failed start runs doctor instead of pretending", "QMS doctor" in r.stdout)

for d in (SRC, APP, SCRATCH):
    shutil.rmtree(d, ignore_errors=True)
for f in ("/tmp/lc.tar.gz",):
    if os.path.exists(f):
        os.remove(f)

print("\n" + "=" * 60)
print(f"PASSED {len(PASS)} / {len(PASS) + len(FAIL)}")
print("ALL GREEN" if not FAIL else "FAILURES: " + ", ".join(FAIL))
print("=" * 60)
sys.exit(0 if not FAIL else 1)
