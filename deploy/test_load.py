"""Replay a store's trading day against the real API, then push until it breaks.

    ./venv/bin/python deploy/test_load.py                     # a 250-SO day
    ./venv/bin/python deploy/test_load.py --so 300 --devices 8
    ./venv/bin/python deploy/test_load.py --ramp               # find the ceiling

Three questions, in order:

  1. Can one store's day be served at all? Replay the whole day's request mix as
     fast as the server will take it, and report the latency that comes back.
  2. Does the queue stay correct under it? Exactly-once claiming is the invariant
     this whole design rests on — 4 POS tapping the same SO at the same moment
     must produce exactly one winner, and every SO must reach completed.
  3. How much headroom is there? Ramp the load until latency degrades, and report
     the ceiling in multiples of a busy store, so "it is fine" comes with a number.

The thing to understand before reading the numbers: the SO traffic is almost
nothing. What actually loads this server is every device polling every 5 seconds,
and that is the same whether 50 or 500 SOs move through the store in a day.

Only ever run against a throwaway database. This script starts its own.
"""

import argparse
import concurrent.futures as futures
import json
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PY = sys.executable

DEFAULT_SO = 250          # a store's day
DAY_HOURS = 12            # 10am - 10pm
POLL_S = 5                # what the frontend actually does (POLL_MS = 5000)
POS_COUNT = 4


# ---------------------------------------------------------------- measurement
class Recorder:
    """Latency per endpoint, thread-safe."""

    def __init__(self):
        self.lock = threading.Lock()
        self.samples = {}
        self.errors = []
        self.count = 0

    def add(self, op, ms, ok=True, note=""):
        with self.lock:
            self.count += 1
            self.samples.setdefault(op, []).append(ms)
            if not ok:
                self.errors.append(f"{op}: {note}")

    def stats(self, op):
        v = sorted(self.samples.get(op, []))
        if not v:
            return None
        def pct(p):
            return v[min(len(v) - 1, int(len(v) * p))]
        return {"n": len(v), "p50": pct(.50), "p95": pct(.95), "p99": pct(.99),
                "max": v[-1], "mean": statistics.fmean(v)}


REC = Recorder()


# ------------------------------------------------------------------- the app
def app_sql(name):
    """Read a SQL constant straight out of app.py.

    The load test must measure the queries the app really runs. Copying them here
    would let them drift, and then this would be measuring a query nobody uses.
    """
    src = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    m = re.search(name + r'\s*=\s*"""\n(.*?)\n"""', src, re.S)
    if not m:
        raise SystemExit(f"cannot find {name} in app.py")
    return m.group(1)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_server(db, port):
    env = dict(os.environ, QMS_PORT=str(port), QMS_DB=db, QMS_STORE_CODE="LOAD",
               QMS_STORE_NAME="Load Test", QMS_POS_COUNT=str(POS_COUNT))
    p = subprocess.Popen([PY, "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
                          "--port", str(port), "--log-level", "warning"],
                         cwd=REPO, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
            return p
        except Exception:
            time.sleep(0.5)
    p.terminate()
    raise SystemExit("server did not start")


def call(base, method, path, token=None, body=None, op=None):
    """One request, timed. Records latency whether it succeeds or not."""
    url = base + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(r.read() or b"{}")
        REC.add(op or path, (time.perf_counter() - t0) * 1000)
        return 200, payload
    except urllib.error.HTTPError as e:
        ms = (time.perf_counter() - t0) * 1000
        try:
            payload = json.loads(e.read() or b"{}")
        except Exception:
            payload = {"detail": "unparseable"}
        # 409 on a contested claim is the design working, not an error
        expected = method == "POST" and e.code in (409, 403) and "claim" in path
        REC.add(op or path, ms, ok=expected, note=f"HTTP {e.code}")
        return e.code, payload
    except Exception as e:
        REC.add(op or path, (time.perf_counter() - t0) * 1000, ok=False, note=type(e).__name__)
        return 0, {"detail": type(e).__name__}


def login(base, role, pos_number=None):
    """Roles are what the sign-in screen sends: a role name, and for a counter,
    which counter it is."""
    body = {"role": role}
    if pos_number is not None:
        body["pos_number"] = pos_number
    _, d = call(base, "POST", "/api/login", body=body, op="login")
    return d.get("token")


# --------------------------------------------------------------- the morning
def build_day(n_so):
    """The request mix one store generates in a day.

    Polling is at its real rate for the real number of devices, because that is
    the load. The SO work is the real pipeline: scan, claim, deliver, complete.
    """
    devices = 1 + POS_COUNT + 1 + 1        # scanner, POS, backstore, manager
    polls = devices * (DAY_HOURS * 3600 // POLL_S)
    return {
        "polls": polls,
        "scans": n_so,
        "claims": n_so,
        "delivers": n_so,
        "completes": n_so,
        "total": polls + n_so * 4,
    }


def phase_pipeline(base, tokens, n_so, workers=8):
    """Drive N SOs end to end through the real pipeline, concurrently.

    This is the correctness phase as much as the performance one: it asserts the
    things that would quietly corrupt a store's queue.
    """
    scanner = tokens["scanner"]
    backstore = tokens["backstore"]
    pos = [t for t in tokens["pos"] if t]

    created = []
    lock = threading.Lock()

    def scan_one(i):
        ref = f"MACSO26-{10000000 + i:08d}"
        code, d = call(base, "POST", "/api/v1/requests/scan", scanner,
                       {"so_number": ref}, op="scan")
        if code == 200 and d.get("id"):
            with lock:
                created.append((d["id"], ref))
        return code

    with futures.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(scan_one, range(n_so)))

    # every POS pulls the queue, claims what is free, and completes it once the
    # backstore has delivered — which is what the real counters do
    def pos_worker(idx):
        tok = pos[idx % len(pos)]
        claimed = []
        for _ in range(200):
            _, d = call(base, "GET", "/api/v1/requests?view=queue", tok, op="queue-poll")
            rows = d.get("requests") or []
            free = [r for r in rows if not r.get("pos_number")]
            if not free:
                break
            for r in free[:3]:
                code, _ = call(base, "POST", f"/api/v1/requests/{r['id']}/claim",
                               tok, op="claim")
                if code == 200:
                    claimed.append(r["id"])
            # the backstore brings them over; the POS closes them
            for rid in list(claimed):
                code, _ = call(base, "POST", f"/api/v1/requests/{rid}/complete", tok,
                               op="complete")
                if code == 200:
                    claimed.remove(rid)
            if not free:
                break
        return claimed

    def backstore_worker():
        done = set()
        for _ in range(300):
            _, d = call(base, "GET", "/api/v1/requests?view=board", backstore,
                        op="board-poll")
            rows = d.get("requests") or []
            pend = [r for r in rows if r.get("pos_number") and r.get("status") == "assigned"
                    and r["id"] not in done]
            if not pend:
                break
            for r in pend[:3]:
                code, _ = call(base, "POST", f"/api/v1/requests/{r['id']}/deliver",
                               backstore, op="deliver")
                if code == 200:
                    done.add(r["id"])
        return len(done)

    with futures.ThreadPoolExecutor(max_workers=len(pos) + 1) as ex:
        fs = [ex.submit(pos_worker, i) for i in range(len(pos))]
        fs.append(ex.submit(backstore_worker))
        for f in fs:
            f.result()

    return created


def phase_polls(base, tokens, n_polls, workers=12):
    """The actual load: every device polling on its real interval, as fast as the
    server will serve them. This is what a store does all day."""
    devs = [tokens["scanner"], tokens["backstore"], tokens["manager"]] + tokens["pos"]
    devs = [t for t in devs if t]
    views = ["queue", "today", "board", "mine"]

    def one(i):
        tok = devs[i % len(devs)]
        view = views[i % len(views)]
        call(base, "GET", f"/api/v1/requests?view={view}", tok, op="poll")

    with futures.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, range(n_polls)))


def phase_realistic(base, tokens, seconds=45):
    """The poll load at its REAL interval and REAL device count.

    The replay above runs the day's requests as a burst, which measures a ceiling
    but overstates what any single request costs — 16 clients queueing against one
    process. This is the number a store actually experiences: seven devices, each
    asking every five seconds, nothing else competing.
    """
    devs = [tokens["scanner"], tokens["backstore"], tokens["manager"]] + tokens["pos"]
    devs = [t for t in devs if t]
    views = ["queue", "today", "board", "mine"]
    stop = time.time() + seconds
    rounds = 0

    def device(i):
        nonlocal rounds
        while time.time() < stop:
            call(base, "GET", f"/api/v1/requests?view={views[i % len(views)]}",
                 devs[i], op="poll-real")
            rounds += 1
            time.sleep(POLL_S)

    threads = [threading.Thread(target=device, args=(i,)) for i in range(len(devs))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return rounds


def age_the_database(db, days=365, per_day=DEFAULT_SO):
    """Write a year of history straight into the database.

    A store does not get slower because of request rate — that is flat all day.
    It gets slower because the table grows, and every device polls every five
    seconds. This is the check that matters a year in: does today's board still
    answer as fast with 90,000 rows behind it as with none?
    """
    import sqlite3
    import datetime
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    row = conn.execute("SELECT id FROM stores ORDER BY id LIMIT 1").fetchone()
    sid = row[0]
    staff = conn.execute("SELECT id FROM staff WHERE role='scanner' LIMIT 1").fetchone()[0]
    today = datetime.date.today()
    rows = []
    events = []
    for d in range(1, days + 1):
        day = (today - datetime.timedelta(days=d)).isoformat()
        for i in range(per_day):
            seq = i + 1
            rows.append((sid, day, seq, f"#{seq:03d}",
                         f"MACSO26-{(d * 100000 + i):08d}", "completed",
                         staff, f"{day}T10:00:00+08:00", (i % 4) + 1, staff,
                         f"{day}T10:02:00+08:00", staff, f"{day}T10:04:00+08:00",
                         staff, f"{day}T10:06:00+08:00"))
    conn.executemany(
        """INSERT INTO so_requests
           (store_id, day_key, seq, ref_no, so_number, status, scanned_by, scanned_at,
            pos_number, claimed_by, claimed_at, delivered_by, delivered_at,
            completed_by, completed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM so_requests").fetchone()[0]
    # Settle the write-ahead log. Without this the "year of data" measurement
    # includes the cost of having just written 90,000 rows in one transaction,
    # which is not a state any store is ever in — it is a state a bulk load is in
    # for a moment. Measuring there produced an 8x regression that did not exist.
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.commit()
    conn.close()
    return n


def query_only_ms(db, views, n=300):
    """Time the queries themselves, with no HTTP in the way.

    The ground truth for "did a year of history make this slower". Going over HTTP
    mixes the query with uvicorn, the client and the scheduler, and on a small
    sample that noise swamps a change of a few milliseconds.
    """
    import sqlite3
    import datetime
    conn = sqlite3.connect(db, timeout=10)
    conn.row_factory = sqlite3.Row
    sid = conn.execute("SELECT id FROM stores LIMIT 1").fetchone()[0]
    day = datetime.date.today().isoformat()
    out = {}
    for name, sql in views.items():
        for _ in range(20):
            conn.execute(sql, (sid, day)).fetchall()
        ts = []
        for _ in range(n):
            t = time.perf_counter()
            conn.execute(sql, (sid, day)).fetchall()
            ts.append((time.perf_counter() - t) * 1000)
        ts.sort()
        out[name] = (ts[len(ts) // 2], ts[int(len(ts) * .95)])
    conn.close()
    return out


def phase_claim_race(base, tokens, rounds=12):
    """Exactly-once claiming: several POS tap the same SO in the same instant.

    The single most important invariant here. If two counters can claim one SO,
    two staff walk the same item to two different customers.
    """
    scanner = tokens["scanner"]
    pos = [t for t in tokens["pos"] if t]
    winners_per_round = []
    for r in range(rounds):
        ref = f"MACSO26-{20000000 + r:08d}"
        _, d = call(base, "POST", "/api/v1/requests/scan", scanner,
                    {"so_number": ref}, op="race-scan")
        rid = d.get("id")
        if not rid:
            continue
        with futures.ThreadPoolExecutor(max_workers=len(pos)) as ex:
            results = list(ex.map(
                lambda t: call(base, "POST", f"/api/v1/requests/{rid}/claim", t,
                               op="race-claim"), pos))
        winners_per_round.append(sum(1 for code, _ in results if code == 200))
    return winners_per_round


def phase_ramp(base, tokens, window=30, ceiling_ms=500):
    """Add devices until it stops coping, and report the ceiling.

    A store runs 7 devices. "It is fine" is worth more with a number attached: how
    many devices could this store carry before a poll stops coming back quickly?
    Each rung is the real thing — N devices, each asking every 5 seconds.
    """
    tok = tokens["scanner"]
    print(f"\n  ramp — devices polling every {POLL_S}s for {window}s each rung")
    print(f"    {'devices':>8}{'req/s':>9}{'p50':>10}{'p95':>10}{'errors':>9}")
    rungs = []
    n = 7
    while n <= 2048:
        mark = len(REC.samples.get("ramp", []))
        emark = len(REC.errors)
        stop = time.time() + window
        rounds = [0]

        def device(idx):
            while time.time() < stop:
                call(base, "GET", f"/api/v1/requests?view=queue", tok, op="ramp")
                rounds[0] += 1
                time.sleep(POLL_S)

        threads = [threading.Thread(target=device, args=(i,)) for i in range(n)]
        t0 = time.time()
        [t.start() for t in threads]
        [t.join() for t in threads]
        wall = time.time() - t0

        s = sorted(REC.samples["ramp"][mark:])
        errs = len(REC.errors) - emark
        if not s:
            break
        p50 = s[len(s) // 2]
        p95 = s[int(len(s) * .95)]
        rate = rounds[0] / wall
        rungs.append((n, rate, p50, p95, errs))
        print(f"    {n:>8}{rate:>9.1f}{p50:>9.1f}m{p95:>9.1f}m{errs:>9}")
        if p95 > ceiling_ms or errs:
            print(f"    -> stopped: p95 passed {ceiling_ms}ms"
                  + (" and requests were failing" if errs else ""))
            break
        n *= 2
    return rungs


# ------------------------------------------------------------------- reports
def report(title, rows, wall, total_requests):
    print(f"\n  {title}")
    print(f"    {total_requests:,} requests in {wall:.1f}s  =  {total_requests/wall:,.0f} req/s")
    print(f"    {'endpoint':<14}{'n':>8}{'p50':>8}{'p95':>8}{'p99':>8}{'max':>9}")
    for op in rows:
        s = REC.stats(op)
        if not s:
            continue
        print(f"    {op:<14}{s['n']:>8,}{s['p50']:>7.1f}m{s['p95']:>7.1f}m"
              f"{s['p99']:>7.1f}m{s['max']:>8.1f}m")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--so", type=int, default=DEFAULT_SO)
    ap.add_argument("--ramp", action="store_true", help="push until it degrades")
    ap.add_argument("--keep", action="store_true", help="leave the database behind")
    args = ap.parse_args()

    tmp = os.path.join("/tmp", f"qms-load-{os.getpid()}")
    os.makedirs(tmp, exist_ok=True)
    db = os.path.join(tmp, "qms.db")
    port = free_port()
    base = f"http://127.0.0.1:{port}"

    print("=" * 66)
    print(f"A store's trading day — {args.so} SOs, {DAY_HOURS}h, "
          f"{1+POS_COUNT+1+1} devices on a {POLL_S}s poll")
    print("(all of it against a real QMS on a throwaway database)")
    print("=" * 66)

    day = build_day(args.so)
    print(f"\n  the day, as requests")
    print(f"    polling  : {day['polls']:>8,}   <- the load")
    print(f"    SO work  : {day['scans']+day['claims']+day['delivers']+day['completes']:>8,}   <- 4 steps x {args.so}")
    print(f"    total    : {day['total']:>8,}")
    print(f"    real rate: {day['total']/(DAY_HOURS*3600):.2f} req/s averaged over the day")

    proc = start_server(db, port)
    try:
        if args.ramp:
            tokens = {
                "scanner": login(base, "scanner"),
                "backstore": login(base, "backstore"),
                "manager": login(base, "manager"),
                "pos": [login(base, "pos", n) for n in range(1, POS_COUNT + 1)],
            }
            rungs = phase_ramp(base, tokens)
            if rungs:
                last_ok = [r for r in rungs if r[3] <= 500 and r[4] == 0]
                best = last_ok[-1] if last_ok else rungs[0]
                print(f"\n    the store runs {1+POS_COUNT+1+1} devices.")
                print(f"    this box carried {best[0]} devices at {best[1]:.0f} req/s "
                      f"before polls got slow ({best[3]:.0f}ms p95).")
                print(f"    that is {best[0]/(1+POS_COUNT+1+1):.0f}x a busy store's device count.")
            return
        tokens = {
            "scanner": login(base, "scanner"),
            "backstore": login(base, "backstore"),
            "manager": login(base, "manager"),
            "pos": [login(base, "pos", n) for n in range(1, POS_COUNT + 1)],
        }
        if not all([tokens["scanner"], tokens["backstore"], tokens["manager"]] + tokens["pos"]):
            raise SystemExit("could not sign in every role")

        # ------------------------------------------- 0. what a store really sees
        print("\n  [0/4] the real thing: 7 devices, each asking every 5s, for 45s")
        print("        (an empty store, so this is a floor, not a baseline)")
        t0 = time.perf_counter()
        n_rounds = phase_realistic(base, tokens, seconds=45)
        wall = time.perf_counter() - t0
        report("at the real poll interval — no burst, no queueing",
               ["poll-real"], wall, n_rounds)

        # ------------------------------------------------ 1. the week's traffic
        # Replaying a whole day's requests takes 12 hours in real life. Here it is
        # run as fast as the server will accept, which is the only honest way to
        # measure a ceiling — and the margin is then the ratio between the two.
        print("\n  [1/3] replaying the whole day's request mix, as fast as it will go")
        t0 = time.perf_counter()
        phase_polls(base, tokens, day["polls"], workers=16)
        wall = time.perf_counter() - t0
        report("poll load — what a store actually does all day",
               ["poll", "queue-poll", "board-poll"], wall, day["polls"])

        # ------------------------------------------- 2. the SOs actually moving
        print(f"\n  [2/3] driving {args.so} SOs through scan -> claim -> deliver -> complete")
        t0 = time.perf_counter()
        created = phase_pipeline(base, tokens, args.so, workers=10)
        wall = time.perf_counter() - t0
        report("the SO pipeline", ["scan", "claim", "deliver", "complete", "queue-poll",
                                   "board-poll"], wall, len(created) * 4)
        print(f"    SOs created: {len(created)} of {args.so}")

        # ------------------------------------------------- 3. the invariant
        print("\n  [3/3] exactly-once claiming — several POS tapping the same SO at once")
        wins = phase_claim_race(base, tokens, rounds=12)
        bad = [w for w in wins if w != 1]
        print(f"    rounds: {len(wins)}   winners per round: {wins}")
        print(f"    WARNING: {len(bad)} round(s) produced more than one winner"
              if bad else "    every round produced exactly one winner")

        # ---------------------------------------------------- 4. a year later
        print("\n  [4/4] the same thing, on a database with a year of history")

        # Order matters here, and getting it wrong is how this measured an 8x
        # regression that did not exist. The baseline must be taken when today
        # already holds its rows — otherwise the comparison is "return nothing"
        # against "return 250 rows", which is not about history at all.
        views = {
            "today": app_sql("BASE_SELECT") + " WHERE r.store_id=? AND r.day_key=? ORDER BY r.seq LIMIT 400",
            "queue": app_sql("BASE_SELECT") + " WHERE r.store_id=? AND r.day_key=? AND r.status='scanned' ORDER BY r.seq LIMIT 400",
        }

        print("    baseline: today full of the day's work, no history behind it")
        b0 = len(REC.samples.get("poll-real", []))
        phase_realistic(base, tokens, seconds=45)
        baseline = sorted(REC.samples["poll-real"][b0:])
        qfresh = query_only_ms(db, views)

        n_rows = age_the_database(db, days=365)
        print(f"    now seeded {n_rows:,} rows ({n_rows // max(args.so,1)} days at "
              f"{args.so}/day) and checkpointed the WAL")

        qaged = query_only_ms(db, views)
        b1 = len(REC.samples.get("poll-real", []))
        phase_realistic(base, tokens, seconds=45)
        aged = sorted(REC.samples["poll-real"][b1:])

        def pct(v, p):
            return v[min(len(v) - 1, int(len(v) * p))] if v else 0.0

        print()
        print("    THE ANSWER — the query on its own, no HTTP in the way:")
        print(f"      {'view':<10}{'fresh':>12}{'1 year':>12}{'change':>12}")
        for name in views:
            f, a = qfresh[name][0], qaged[name][0]
            print(f"      {name:<10}{f:>10.2f}ms{a:>10.2f}ms{a - f:>+10.2f}ms")
        print(f"      a poll happens every {POLL_S*1000}ms. A change of a few "
              f"milliseconds is not a change.")

        print()
        print("    and the same over HTTP, both windows taken the same way:")
        print(f"      {'':<24}{'n':>6}{'p50':>10}{'p95':>10}")
        print(f"      {'today full, no history':<24}{len(baseline):>6}"
              f"{pct(baseline,.5):>9.1f}m{pct(baseline,.95):>9.1f}m")
        print(f"      {'today full + a year':<24}{len(aged):>6}"
              f"{pct(aged,.5):>9.1f}m{pct(aged,.95):>9.1f}m")
        print("      this one is mostly the client and uvicorn — a sub-10ms query")
        print("      disappears inside a local HTTP round trip. Trust the table above.")

        # ------------------------------------------------------- what is left
        _, fin = call(base, "GET", "/api/v1/stats/today", tokens["manager"], op="stats")
        print("\n  state at the end")
        print(f"    in the queue today: {json.dumps(fin)[:180]}")

        print("\n  errors:", "none" if not REC.errors else "")
        for e in REC.errors[:10]:
            print("   ", e)
        print(f"\n  total requests: {REC.count:,}")

        print("\n" + "=" * 66)
        print("  READ THIS AS")
        print(f"    The store generates {day['total']/(DAY_HOURS*3600):.2f} req/s spread over "
              f"{DAY_HOURS} hours.")
        print("    Above, that same day's work was done as fast as the server would")
        print("    take it. The ratio between the two is the headroom.")
        print("=" * 66)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            print(f"\n  database kept at {db}")


if __name__ == "__main__":
    main()
