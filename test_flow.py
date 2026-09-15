"""End-to-end + concurrency test for QMS.

    ./venv/bin/python test_flow.py [base_url]

Two invariants matter most here:

  1. `concurrent_claim` — fire N simultaneous claims at the same SO and require
     exactly ONE winner. That is the operation a CSV "database" cannot do.
  2. `fifo_order` — My SOs is ordered by when the SCANNER scanned, not by when a
     POS happened to claim. Claiming out of order must not reshuffle the queue.
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


def scan(tok, so):
    st, d = call("POST", "/api/v1/requests/scan", {"so_number": so}, tok)
    assert st == 200, (st, d)
    return d


print("== health ==")
st, h = call("GET", "/api/health")
print(" ", h)
check("health ok", st == 200 and h.get("ok"))
check("health reports all three thresholds",
      all(k in h for k in ("stale_minutes", "sla_minutes", "complete_minutes")),
      {k: h.get(k) for k in ("stale_minutes", "sla_minutes", "complete_minutes")})

print("\n== role logins ==")
T = {}
for role in ("scanner", "backstore", "manager"):
    T[role] = login(role)
for n in range(1, h.get("pos_count", 4) + 1):
    T["pos%d" % n] = login("pos", n)
print("  signed in as:", ", ".join(sorted(T)))

print("\n== role guards ==")
st, _ = call("POST", "/api/v1/requests/scan", {"so_number": "MACSO26-00000001"}, T["pos1"])
check("POS cannot scan -> 403", st == 403, st)
st, _ = call("POST", "/api/v1/requests/scan", {"so_number": "MACSO26-00000001"}, T["backstore"])
check("Backstore cannot scan -> 403", st == 403, st)
st, _ = call("GET", "/api/v1/stats/today", token=T["scanner"])
check("Scanner cannot read stats -> 403", st == 403, st)

print("\n== scan ==")
SO = "MACSO26-00999999"
t = scan(T["scanner"], SO.lower())
check("scanner scan ok", t["status"] == "scanned")
check("SO normalised to uppercase", t.get("so_number") == SO, t.get("so_number"))
check("format_ok true for a real MACSO format", t.get("format_ok") is True)
RID = t["id"]

t2 = scan(T["scanner"], SO)
check("rescan -> duplicate, no new row", t2["duplicate"] is True and t2["id"] == RID)
bad = scan(T["scanner"], "1234567890")
check("non-MACSO format accepted but flagged", bad.get("format_ok") is False)

print("\n== CONCURRENT CLAIM (the invariant a CSV cannot hold) ==")
results = []
lock = threading.Lock()


def try_claim(tok, who):
    st, d = call("POST", "/api/v1/requests/%d/claim" % RID, None, tok)
    with lock:
        results.append((who, st, d.get("detail")))


threads = [threading.Thread(target=try_claim, args=(T["pos%d" % n], "pos%d" % n))
           for n in range(1, h.get("pos_count", 4) + 1)]
for th in threads:
    th.start()
for th in threads:
    th.join()

wins = [r for r in results if r[1] == 200]
losses = [r for r in results if r[1] == 409]
for w in results:
    print("   %-5s -> HTTP %s%s" % (w[0], w[1], "" if w[1] == 200 else "  (%s)" % w[2]))
check("exactly ONE POS won the claim", len(wins) == 1, "%d winners" % len(wins))
check("the rest got 409", len(losses) == len(threads) - 1, "%d conflicts" % len(losses))
check("loser message names the winner",
      any(d and "POS" in str(d) for _, s, d in results if s == 409),
      [d for _, s, d in results if s == 409][:1])

st, row = call("GET", "/api/v1/requests?view=today", None, T["backstore"])
me = [r for r in row["requests"] if r["id"] == RID][0]
winner_pos = me["pos_number"]
check("DB holds exactly one POS number", bool(winner_pos), winner_pos)

print("\n== claim moves the SO out of the queue and into My SOs ==")
st, qv = call("GET", "/api/v1/requests?view=queue", None, T["pos1"])
check("claimed SO is GONE from the shared queue",
      not [r for r in qv["requests"] if r["id"] == RID])

st, mv = call("GET", "/api/v1/requests?view=mine", None, T["pos%d" % winner_pos])
inmine = [r for r in mv["requests"] if r["id"] == RID]
check("claimed SO appears in the winner's My SOs", len(inmine) == 1, "%d rows" % len(inmine))
check("...with status PENDING STOCK (assigned)", inmine and inmine[0]["status"] == "assigned")

st, mv2 = call("GET", "/api/v1/requests?view=mine", None,
               T["pos%d" % (1 if winner_pos != 1 else 2)])
check("it does NOT appear in another POS's My SOs",
      not [r for r in mv2["requests"] if r["id"] == RID])

print("\n== backstore hands it over ==")
st, dv = call("POST", "/api/v1/requests/%d/deliver" % RID, None, T["pos%d" % winner_pos])
check("POS can no longer mark delivered -> 403", st == 403, st)
st, dv = call("POST", "/api/v1/requests/%d/deliver" % RID, None, T["backstore"])
check("backstore deliver ok", st == 200 and dv["status"] == "delivered", st)
st, dv = call("POST", "/api/v1/requests/%d/deliver" % RID, None, T["backstore"])
check("double deliver -> 409", st == 409, st)

st, mv3 = call("GET", "/api/v1/requests?view=mine", None, T["pos%d" % winner_pos])
still = [r for r in mv3["requests"] if r["id"] == RID]
check("STILL in My SOs after delivery (now DELIVERED)",
      len(still) == 1 and still[0]["status"] == "delivered")

st, ac = call("GET", "/api/v1/requests?view=atcounter", None, T["backstore"])
check("shows up in the backstore At-Counter list",
      [r for r in ac["requests"] if r["id"] == RID])

print("\n== POS closes it ==")
st, cv = call("POST", "/api/v1/requests/%d/complete" % RID, None,
              T["pos%d" % (1 if winner_pos != 1 else 2)])
check("another POS cannot complete it -> 403", st == 403, st)
st, cv = call("POST", "/api/v1/requests/%d/complete" % RID, None, T["pos%d" % winner_pos])
check("the owning POS can complete it", st == 200 and cv["status"] == "completed", st)
check("completion is timestamped", bool(cv.get("completed_at")))
st, cv = call("POST", "/api/v1/requests/%d/complete" % RID, None, T["pos%d" % winner_pos])
check("completing twice -> 409", st == 409, st)

st, mv4 = call("GET", "/api/v1/requests?view=mine", None, T["pos%d" % winner_pos])
check("completed SO left My SOs", not [r for r in mv4["requests"] if r["id"] == RID])
st, dv2 = call("GET", "/api/v1/requests?view=completed", None, T["pos%d" % winner_pos])
check("completed SO is in the Completed tab",
      [r for r in dv2["requests"] if r["id"] == RID])
st, qv2 = call("GET", "/api/v1/requests?view=queue", None, T["pos1"])
check("and it is not back in the queue", not [r for r in qv2["requests"] if r["id"] == RID])

print("\n== FIFO: My SOs is ordered by SCAN time, not claim time ==")
a = scan(T["scanner"], "MACSO26-00777001")   # scanned first
b = scan(T["scanner"], "MACSO26-00777002")   # scanned second
call("POST", "/api/v1/requests/%d/claim" % b["id"], None, T["pos3"])   # but claimed FIRST
call("POST", "/api/v1/requests/%d/claim" % a["id"], None, T["pos3"])   # claimed second
st, mv5 = call("GET", "/api/v1/requests?view=mine", None, T["pos3"])
order = [r["so_number"] for r in mv5["requests"]
         if r["so_number"] in ("MACSO26-00777001", "MACSO26-00777002")]
check("earlier scan sorts first even though it was claimed later",
      order == ["MACSO26-00777001", "MACSO26-00777002"], order)
seqs = [r["seq"] for r in mv5["requests"]]
check("the whole My SOs list is ascending by arrival", seqs == sorted(seqs), seqs)

print("\n== completing without ever claiming ==")
c = scan(T["scanner"], "MACSO26-00777003")
st, _ = call("POST", "/api/v1/requests/%d/complete" % c["id"], None, T["pos1"])
check("POS cannot complete an unclaimed SO -> 403", st == 403, st)
st, _ = call("POST", "/api/v1/requests/%d/deliver" % c["id"], None, T["backstore"])
check("backstore may still deliver an unclaimed SO (flagged)", st == 200, st)

print("\n== release ==")
d = scan(T["scanner"], "MACSO26-00777004")
call("POST", "/api/v1/requests/%d/claim" % d["id"], None, T["pos2"])
st, _ = call("POST", "/api/v1/requests/%d/release" % d["id"], None, T["pos3"])
check("another POS cannot release it -> 409", st == 409, st)
st, rl = call("POST", "/api/v1/requests/%d/release" % d["id"], None, T["pos2"])
check("the owner can release it", st == 200 and rl["status"] == "scanned"
      and rl["pos_number"] is None, st)

print("\n== cancel ==")
e = scan(T["scanner"], "MACSO26-00777005")
st, cc = call("POST", "/api/v1/requests/%d/cancel" % e["id"], {"reason": "test"}, T["scanner"])
check("scanner can cancel their own scan", st == 200 and cc["status"] == "cancelled", st)
st, _ = call("POST", "/api/v1/requests/%d/cancel" % e["id"], {"reason": "again"}, T["scanner"])
check("double cancel -> 409", st == 409, st)

print("\n== views ==")
for view, tok in (("queue", T["pos1"]), ("mine", T["pos1"]), ("completed", T["pos1"]),
                  ("todeliver", T["backstore"]), ("atcounter", T["backstore"]),
                  ("mine", T["scanner"]), ("today", T["manager"]), ("all", T["manager"])):
    st, dv = call("GET", "/api/v1/requests?view=" + view, None, tok)
    check("view %-10s -> %3d rows" % (view, len(dv.get("requests", []))), st == 200, st)

print("\n== audit trail ==")
st, dv = call("GET", "/api/v1/requests/%d/events" % RID, None, T["backstore"])
names = [x["event"] for x in dv["events"]]
check("audit has scanned/claimed/delivered/completed",
      all(x in names for x in ("scanned", "claimed", "delivered", "completed")), names)

print("\n== stats + csv ==")
st, s = call("GET", "/api/v1/stats/today", None, T["manager"])
check("stats ok", st == 200 and s["total"] > 0, st)
print("   ", json.dumps({k: v for k, v in s.items()
                          if k in ("total", "scanned", "assigned", "delivered", "completed",
                                   "attention", "avg_total", "avg_wait_stock",
                                   "avg_wait_complete")}))
req = urllib.request.Request(BASE + "/api/v1/stats/export.csv",
                            headers={"Authorization": "Bearer " + T["manager"]})
with urllib.request.urlopen(req, timeout=20) as r:
    csv_body = r.read().decode()
check("csv has the new completion columns",
      "COMPLETED_AT" in csv_body and "WAIT_COMPLETE_MIN" in csv_body)
check("csv export has header + rows", csv_body.count("\r\n") > 2)

print("\n" + "=" * 60)
print("PASSED %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("ALL GREEN")
