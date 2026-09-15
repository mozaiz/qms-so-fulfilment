"""End-to-end + concurrency test for QMS v3.

Run against a live server:  ./venv/bin/python test_flow.py [base_url]

The important test is `concurrent_claim`: two POS devices tapping the same SO at
the same instant must produce exactly ONE winner. That is the invariant a CSV
"database" cannot hold.
"""
import json
import sys
import threading
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8099").rstrip("/")
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


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  -- " + str(detail)) if detail else ""))


def login(role, pos=None):
    body = {"role": role}
    if pos:
        body["pos_number"] = pos
    st, d = call("POST", "/api/login", body)
    assert st == 200, (st, d)
    return d["token"]


print("== health ==")
st, h = call("GET", "/api/health")
print(" ", h)
check("health ok", st == 200 and h.get("ok"))

print("\n== role logins ==")
T = {}
for role in ("scanner", "backstore", "manager"):
    T[role] = login(role)
    print("  %-9s ok" % role)
for n in range(1, h.get("pos_count", 4) + 1):
    T["pos%d" % n] = login("pos", n)
    print("  pos%d      ok" % n)

print("\n== role guards ==")
st, _ = call("POST", "/api/v1/requests/scan", {"so_number": "MACSO26-00000001"}, T["pos1"])
check("POS cannot scan -> 403", st == 403, st)
st, _ = call("POST", "/api/v1/requests/scan", {"so_number": "MACSO26-00000001"}, T["backstore"])
check("Backstore cannot scan -> 403", st == 403, st)
st, _ = call("GET", "/api/v1/stats/today", token=T["scanner"])
check("Scanner cannot read stats -> 403", st == 403, st)

print("\n== scan ==")
SO = "MACSO26-00999999"
st, t = call("POST", "/api/v1/requests/scan", {"so_number": SO.lower()}, T["scanner"])
check("scanner scan ok", st == 200 and t["status"] == "scanned", st)
check("SO normalised to uppercase", t.get("so_number") == SO, t.get("so_number"))
check("format_ok true for real MACSO format", t.get("format_ok") is True)
RID = t["id"]

st, t2 = call("POST", "/api/v1/requests/scan", {"so_number": SO}, T["scanner"])
check("rescan -> duplicate, no new row", st == 200 and t2["duplicate"] is True and t2["id"] == RID)

st, bad = call("POST", "/api/v1/requests/scan", {"so_number": "1234567890"}, T["scanner"])
check("non-MACSO format accepted but flagged", st == 200 and bad.get("format_ok") is False,
      bad.get("format_ok"))

print("\n== CONCURRENT CLAIM (the invariant CSV cannot hold) ==")
results = []
lock = threading.Lock()


def try_claim(tok, who):
    st, d = call("POST", "/api/v1/requests/%d/claim" % RID, None, tok)
    with lock:
        results.append((who, st, d.get("pos_number"), d.get("detail")))


threads = [threading.Thread(target=try_claim, args=(T["pos1"], "pos1")),
           threading.Thread(target=try_claim, args=(T["pos2"], "pos2")),
           threading.Thread(target=try_claim, args=(T["pos3"], "pos3")),
           threading.Thread(target=try_claim, args=(T["pos4"], "pos4"))]
for th in threads:
    th.start()
for th in threads:
    th.join()

wins = [r for r in results if r[1] == 200]
losses = [r for r in results if r[1] == 409]
for w in results:
    print("   %-5s -> HTTP %s%s" % (w[0], w[1], "" if w[1] == 200 else "  (%s)" % w[2:3]))
check("exactly ONE POS won the claim", len(wins) == 1, "%d winners" % len(wins))
check("exactly THREE got 409", len(losses) == 3, "%d conflicts" % len(losses))

st, row = call("GET", "/api/v1/requests?view=today", None, T["backstore"])
me = [r for r in row["requests"] if r["id"] == RID][0]
check("DB holds exactly one POS number", bool(me["pos_number"]))
check("loser message names the winner",
      any(d and "POS" in str(d) for _, s, _, d in results if s == 409),
      [d for _, s, _, d in results if s == 409][:1])

print("\n== deliver ==")
st, d = call("POST", "/api/v1/requests/%d/deliver" % RID, None, T["backstore"])
check("backstore deliver ok", st == 200 and d["status"] == "delivered", st)
st, d = call("POST", "/api/v1/requests/%d/deliver" % RID, None, T["backstore"])
check("double deliver -> 409", st == 409, st)

print("\n== deliver with no POS -> warning ==")
st, t3 = call("POST", "/api/v1/requests/scan", {"so_number": "MACSO26-00999998"}, T["scanner"])
st, d = call("POST", "/api/v1/requests/%d/deliver" % t3["id"], None, T["backstore"])
check("deliver without POS allowed", st == 200, st)
check("warning flag returned", d.get("warning") == "Delivered without a POS", d.get("warning"))
check("no_pos_warning set on read", d.get("no_pos_warning") is True)

print("\n== release ==")
st, t4 = call("POST", "/api/v1/requests/scan", {"so_number": "MACSO26-00999997"}, T["scanner"])
st, c = call("POST", "/api/v1/requests/%d/claim" % t4["id"], None, T["pos2"])
check("pos2 claimed", st == 200 and c["pos_number"] == 2, st)
st, r = call("POST", "/api/v1/requests/%d/release" % t4["id"], None, T["pos3"])
check("other POS cannot release it -> 409", st == 409, st)
st, r = call("POST", "/api/v1/requests/%d/release" % t4["id"], None, T["pos2"])
check("owner can release", st == 200 and r["status"] == "scanned" and r["pos_number"] is None, st)

print("\n== cancel ==")
st, t5 = call("POST", "/api/v1/requests/scan", {"so_number": "MACSO26-00999996"}, T["scanner"])
st, c = call("POST", "/api/v1/requests/%d/cancel" % t5["id"], {"reason": "test"}, T["scanner"])
check("scanner can cancel own scan", st == 200 and c["status"] == "cancelled", st)
st, c = call("POST", "/api/v1/requests/%d/cancel" % t5["id"], {"reason": "again"}, T["scanner"])
check("double cancel -> 409", st == 409, st)

print("\n== views ==")
for view, tok, who in (("unclaimed", T["pos1"], "pos"),
                       ("todeliver", T["backstore"], "backstore"),
                       ("mine", T["scanner"], "scanner"),
                       ("today", T["manager"], "manager")):
    st, d = call("GET", "/api/v1/requests?view=" + view, None, tok)
    check("view %-10s -> %d rows" % (view, len(d.get("requests", []))), st == 200, st)

print("\n== live queue keeps claimed SOs (they are called by SO number) ==")
st, q1 = call("POST", "/api/v1/requests/scan", {"so_number": "MACSO26-00888001"}, T["scanner"])
qid = q1["id"]
st, qv = call("GET", "/api/v1/requests?view=queue", None, T["pos1"])
check("queue view reachable", st == 200, st)
in_q = [r for r in qv["requests"] if r["id"] == qid]
check("unclaimed SO is in the queue", len(in_q) == 1 and in_q[0]["status"] == "scanned")

st, _ = call("POST", "/api/v1/requests/%d/claim" % qid, None, T["pos1"])
st, qv2 = call("GET", "/api/v1/requests?view=queue", None, T["pos1"])
in_q2 = [r for r in qv2["requests"] if r["id"] == qid]
check("claimed SO STAYS in the queue", len(in_q2) == 1, "%d rows" % len(in_q2))
check("...and shows which POS has it", in_q2 and in_q2[0]["pos_number"] == 1)

st, _ = call("POST", "/api/v1/requests/%d/deliver" % qid, None, T["backstore"])
st, qv3 = call("GET", "/api/v1/requests?view=queue", None, T["pos1"])
check("completed SO leaves the queue",
      not [r for r in qv3["requests"] if r["id"] == qid])

print("\n== POS can complete its own SO ==")
st, p1 = call("POST", "/api/v1/requests/scan", {"so_number": "MACSO26-00888002"}, T["scanner"])
pid = p1["id"]
call("POST", "/api/v1/requests/%d/claim" % pid, None, T["pos2"])
st, d = call("POST", "/api/v1/requests/%d/deliver" % pid, None, T["pos3"])
check("another POS cannot complete it -> 403", st == 403, st)
st, d = call("POST", "/api/v1/requests/%d/deliver" % pid, None, T["pos2"])
check("the owning POS can complete it", st == 200 and d["status"] == "delivered", st)
st, d = call("POST", "/api/v1/requests/%d/deliver" % pid, None, T["pos2"])
check("completing twice -> 409", st == 409, st)

st, p2 = call("POST", "/api/v1/requests/scan", {"so_number": "MACSO26-00888003"}, T["scanner"])
st, d = call("POST", "/api/v1/requests/%d/deliver" % p2["id"], None, T["pos1"])
check("POS cannot complete an SO nobody claimed -> 403", st == 403, st)

print("\n== audit trail ==")
st, d = call("GET", "/api/v1/requests/%d/events" % RID, None, T["backstore"])
names = [e["event"] for e in d["events"]]
check("audit has scanned/claimed/delivered", all(x in names for x in ("scanned", "claimed", "delivered")),
      names)

print("\n== stats + csv ==")
st, s = call("GET", "/api/v1/stats/today", None, T["manager"])
check("stats ok", st == 200 and s["total"] > 0, st)
print("   ", json.dumps({k: v for k, v in s.items()
                          if k in ("total", "scanned", "assigned", "delivered",
                                   "attention", "no_pos_delivered", "avg_total")}))
req = urllib.request.Request(BASE + "/api/v1/stats/export.csv",
                            headers={"Authorization": "Bearer " + T["manager"]})
with urllib.request.urlopen(req, timeout=20) as r:
    csv_body = r.read().decode()
check("csv export has header + rows", "SO_NUMBER" in csv_body and csv_body.count("\r\n") > 2)

print("\n" + "=" * 58)
print("PASSED %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("ALL GREEN")
