"""Reset the database and create a realistic demo state.

Covers every stage of the handover so the UI can be checked at a glance:

    scanned -> assigned (Pending Stock) -> delivered (At Counter) -> completed
"""
import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Kuala_Lumpur")
DB = os.environ.get("QMS_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "qms.db"))

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
conn.execute("DELETE FROM so_events")
conn.execute("DELETE FROM so_requests")
conn.execute("DELETE FROM sessions")
conn.commit()

store_id = conn.execute("SELECT id FROM stores LIMIT 1").fetchone()["id"]
by_code = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM staff").fetchall()}
scanner = by_code["scanner"]
backstore = by_code["backstore"]

now = datetime.now(TZ)
day = now.strftime("%Y-%m-%d")


def ts(mins_ago):
    return (now - timedelta(minutes=mins_ago)).isoformat(timespec="seconds")


# (so_number, scan_ago, pos, claim_ago, deliver_ago, complete_ago, status)
SEED = [
    # nobody has claimed these yet -> the 18 min one trips the STALE flag
    ("MACSO26-00142463", 18, None, None, None, None, "scanned"),
    ("MACSO26-00142464",  6, None, None, None, None, "scanned"),
    # claimed, backstore still fetching -> PENDING STOCK
    ("MACSO26-00142465",  9, 3, 7, None, None, "assigned"),
    ("MACSO26-00142466",  4, 1, 3, None, None, "assigned"),
    # at the counter, POS has not closed it -> trips the "awaiting complete" flag
    ("MACSO26-00142461", 25, 2, 24, 12, None, "delivered"),
    # completed normally
    ("MACSO26-00142460", 40, 4, 39, 37, 35, "completed"),
    ("MACSO26-00142458", 50, 1, 48, 45, 42, "completed"),
    # cancelled
    ("MACSO26-00142450", 30, None, None, None, None, "cancelled"),
]

for i, (so, scan_ago, pos, claim_ago, deliv_ago, comp_ago, status) in enumerate(SEED, start=1):
    ref = "#%03d" % i
    claimed_by = by_code.get("pos%d" % pos) if pos else None
    claimed_at = ts(claim_ago) if claim_ago is not None else None
    delivered_at = ts(deliv_ago) if deliv_ago is not None else None
    completed_at = ts(comp_ago) if comp_ago is not None else None
    cancelled_at = ts(scan_ago - 2) if status == "cancelled" else None

    cur = conn.execute(
        """INSERT INTO so_requests
           (store_id, day_key, seq, ref_no, so_number, status,
            scanned_by, scanned_at, pos_number, claimed_by, claimed_at,
            delivered_by, delivered_at, completed_by, completed_at,
            cancelled_by, cancelled_at, cancel_reason, note)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (store_id, day, i, ref, so, status,
         scanner, ts(scan_ago), pos, claimed_by, claimed_at,
         backstore if delivered_at else None, delivered_at,
         claimed_by if completed_at else None, completed_at,
         scanner if cancelled_at else None, cancelled_at,
         "Customer changed their mind" if status == "cancelled" else None, None),
    )
    rid = cur.lastrowid

    ev = [("scanned", scanner, ts(scan_ago))]
    if claimed_at:
        ev.append(("claimed", claimed_by, claimed_at))
    if delivered_at:
        ev.append(("delivered", backstore, delivered_at))
    if completed_at:
        ev.append(("completed", claimed_by, completed_at))
    if cancelled_at:
        ev.append(("cancelled", scanner, cancelled_at))
    for name, actor, at in ev:
        conn.execute(
            "INSERT INTO so_events (request_id, event, actor_id, at, meta) VALUES (?,?,?,?,?)",
            (rid, name, actor, at, None))

conn.commit()

print("Seeded", day)
for r in conn.execute(
    "SELECT ref_no, so_number, status, pos_number, scanned_at FROM so_requests ORDER BY seq"
):
    print("  %-5s %-20s %-10s POS=%-4s scan=%s" % (
        r["ref_no"], r["so_number"], r["status"], r["pos_number"] or "-", r["scanned_at"]))
counts = {}
for r in conn.execute("SELECT status, COUNT(*) c FROM so_requests GROUP BY status"):
    counts[r["status"]] = r["c"]
print("  stages:", counts)
conn.close()
