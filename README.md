# QMS — SO Fulfilment

Internal tool for Machines stores. A customer's Sales Order barcode gets scanned
at the front, the backstore picks the item and walks it to the right POS counter,
and everyone can see where it is at any moment.

```
SCANNER              P1 · P2 · P3 · P4                  BACKSTORE
scan barcode SO      claim it to their counter           walks it to that counter
MACSO26-00142463     -> PENDING STOCK                    -> marks it DELIVERED
   │                       │                                  │
   └──► Queue ─────────────┘                                  │
        (oldest scan first)                                   │
                     POS marks COMPLETE ◄─────────────────────┘
                     -> Completed, out of the queue
```

Four stages, each timed separately, so the store can see exactly where a delay is:

| Stage | On the screen | Who moves it on |
|---|---|---|
| `scanned` | **WAITING** — in the Queue | a POS claims it |
| `assigned` | **PENDING STOCK** — in My SOs | backstore delivers |
| `delivered` | **DELIVERED** — in My SOs, at the counter | the POS completes it |
| `completed` | **COMPLETED** | — it has left the queue |

Runs on **one small computer inside the store**. Every device in that store just
opens a web address. No cloud account, no subscription, **no data leaves the
building**.

---

## Quick start

```bash
./install.sh          # Linux / macOS — installs, starts on boot, prints the address
```

Windows: copy the folder and double-click `deploy\Install QMS (Windows).cmd`.

Or grab a ready-made archive from the **Releases** page — it contains everything
an outlet needs and nothing else.

Full step-by-step, written for whoever is setting it up at the outlet:
**[INSTALL.md](INSTALL.md)**

Then print the wall sign — this is the entire staff training:

```bash
./venv/bin/python make_qr_card.py     # -> qr_card_<STORE>.pdf (A4)
```

To cut a release:

```bash
git tag v0.3.1 && git push origin v0.3.1
```

CI builds the archives, **installs one in a clean container and boots it** to prove
it works, then attaches `qms-<version>.zip`, `.tar.gz` and `SHA256SUMS.txt` to the
release.

Licence: MIT — see [LICENSE](LICENSE).

---

## Access, and the camera catch

| URL | Secure context | Camera |
|---|---|---|
| `http://localhost:8099` (on the QMS machine) | ✅ | **works** |
| `http://192.168.x.x:8099` (other devices) | ❌ | blocked |
| `https://…` (behind a tunnel) | ✅ | works |

Browsers only hand over a camera on a **secure context**. `localhost` counts as
secure; a LAN IP does not. Verified in a real browser:

```
localhost      -> isSecureContext True,  mediaDevices object
192.168.0.25   -> isSecureContext False, mediaDevices undefined
https tunnel   -> isSecureContext True,  mediaDevices object
```

So the **SCANNER** role belongs on the QMS computer itself — which is also the
zero-configuration deployment. **POS** and **BACKSTORE** never use the camera, so
they can be on any phone or tablet. QMS detects the insecure case and shows a
clear warning instead of silently failing.

Want the camera on a phone? Put HTTPS in front (a Cloudflare Tunnel is free), or
use Manual Entry.

---

## Never store this in a spreadsheet

The obvious idea is a shared CSV that everyone appends to. It does not survive
contact with a busy shop floor — 1 scanner, 4 POS screens and a backstore means
six writers:

| Problem with a CSV | What actually happens |
|---|---|
| No atomic write | Two updates at once and one is silently lost |
| No lock | Two POS claim the same SO and **both** think they won |
| No transaction | "read → check → write" is three steps with a gap between them |
| No crash safety | Power cut mid-write truncates the file; the day is gone |
| Rewrite per change | One scan rewrites the whole file |
| No index | Finding one SO means reading everything |
| Torn backups | Copying a file mid-write gives a backup you cannot restore |

**SQLite is also a single file**, ships in the Python standard library, needs no
installer, and in WAL mode gives one writer plus many concurrent readers. Six
human-driven clients is nothing to it.

And the CSV you actually wanted still exists — **Export CSV** on the Stats
screen, plus a nightly dump if you want it.

### The proof

`test_flow.py` fires **four simultaneous claims at the same SO** using threads and
requires exactly one winner:

```
pos4 -> HTTP 200   Winner
pos1 -> HTTP 409   (Already claimed by POS 4)
pos2 -> HTTP 409   (Already claimed by POS 4)
pos3 -> HTTP 409   (Already claimed by POS 4)
```

One statement, no lock table:

```sql
UPDATE so_requests
   SET status='assigned', pos_number=?, claimed_by=?, claimed_at=?
 WHERE id=? AND store_id=? AND status='scanned';
-- rowcount 1 -> won     rowcount 0 -> someone else won -> 409
```

Reinforced at the database level, independent of application logic:

```sql
CREATE UNIQUE INDEX idx_open_so ON so_requests(store_id, day_key, so_number)
  WHERE status IN ('scanned','assigned');
```

---

## Sign-in

A role picker, no PIN (store-internal by design):

```
SCANNER   P1   P2
P3   P4   BACKSTORE
      ⚙ Manager
```

Tap a role and you are in. **Switch Role** logs out. The device remembers the
choice until switched. Manager sits behind a small link because it is not part of
the daily flow.

| Role | Tabs | Can do |
|---|---|---|
| **Scanner** | Scan · My Scans | Scan SOs, see what they scanned, cancel their own |
| **P1–P4** | Queue · My SOs · Completed | Claim an SO, watch for DELIVERED, mark it complete |
| **Backstore** | To Deliver · At Counter · Completed | Deliver to the POS, see what is still open, export |
| **Manager** | All · Stats · Setup | Everything, plus POS counters |

### Claiming moves an SO from Queue to My SOs

The Queue holds only what nobody has taken yet. The moment a POS claims an SO it
leaves the shared Queue and appears in **that counter's My SOs**, where it stays
through both remaining stages — `PENDING STOCK` while the backstore is fetching,
`DELIVERED` once the item is physically at the counter.

**My SOs is ordered by scan time, not claim time.** This is a queue: the customer
who arrived first is served first, so claiming out of order must not reshuffle
anything. `seq` is the daily arrival counter, which is what makes that exact.

Roles are enforced **server-side** — a POS calling the scan endpoint gets `403`,
a scanner reading stats gets `403`.

### Adding POS counters

Default is **4**. Manager → **Setup** → **+ Add POS** creates POS 5, then 6, and so
on (cap 20). The new counter appears on every device's sign-in screen immediately,
no restart. **− Remove last** switches the newest one off; the staff row is
deactivated rather than deleted, so the audit trail survives.

---

## Statuses and attention flags

`scanned` → `assigned` → `delivered`, or `cancelled`

Shown to staff as **WAITING / CLAIMED / DELIVERED / CANCELLED**.

### Nothing leaves the queue until the POS closes it

Customers are called out by SO number, so the queue has to be the honest list of
who is still waiting. An SO disappears from it only on `completed`.

Nothing is ever deleted — every view is a filter over the same rows. "The SO
vanished" can therefore only ever mean "someone completed it", which is what makes
it trustworthy when a customer argues.

### Who can do what

Two confirmations at handover, so the store knows the customer actually received
the goods: the **backstore** says "walked it over", the **POS** says "customer has
it". The boundary is enforced in the database, not just by hiding buttons:

- the **owning** POS → may complete
- a **different** POS → `403`
- an SO **no POS ever claimed** → `403` for a POS, backstore may still deliver it (flagged)
- the backstore → may deliver, may **not** complete

Three things turn a card **red** — flagged, never auto-actioned:

- **Stale** — scanned but no POS claimed it within `QMS_STALE_MIN` (15 min)
- **Over SLA** — claimed but backstore has not delivered within `QMS_SLA_MIN` (10 min)
- **Awaiting completion** — sitting at the counter and the POS has not closed it
  within `QMS_COMPLETE_MIN` (10 min). This is the one that matters most: the
  customer has their goods but nobody pressed the button, so the queue looks longer
  than it is.
- **Delivered without a POS** — allowed, but marked

Nothing is ever auto-cancelled. Someone on the floor decides.

Likewise, the **backstore can tick delivered without any POS claiming it** — legal,
but flagged. Hard-blocking that would look correct in a spec and strand staff with
a customer waiting.

---

## Configuration

Everything lives in `qms.env` (see `qms.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `QMS_PORT` | `8099` | Port |
| `QMS_DB` | `<dir>/qms.db` | SQLite file |
| `QMS_STORE_CODE` | `MCSQ01` | Store code, shown on the QR sign |
| `QMS_STORE_NAME` | `Machines Store` | Display name |
| `QMS_POS_COUNT` | `4` | Counters on a fresh install |
| `QMS_MAX_POS` | `20` | Ceiling for the Add POS button |
| `QMS_STALE_MIN` | `15` | Scanned-but-unclaimed turns red |
| `QMS_SLA_MIN` | `10` | Claimed-but-undelivered turns red |
| `QMS_COMPLETE_MIN` | `10` | At-the-counter-but-not-completed turns red |
| `QMS_DEDUPE_MIN` | `5` | Ignore a repeat scan within this window |
| `QMS_TZ` | `Asia/Kuala_Lumpur` | Timezone |

---

## API

```
POST   /api/login                     {role, pos_number?}
POST   /api/logout
GET    /api/me
POST   /api/v1/requests/scan          {so_number, note?}   scanner, manager
GET    /api/v1/requests?view=queue|mine|completed|todeliver|atcounter|today|all
POST   /api/v1/requests/{id}/claim    atomic compare-and-swap   pos, manager
POST   /api/v1/requests/{id}/release  undo a claim              pos, manager
POST   /api/v1/requests/{id}/deliver  hand the item over       backstore, manager
POST   /api/v1/requests/{id}/complete close the entry          owning pos, manager
POST   /api/v1/requests/{id}/cancel   {reason?}                 scanner, manager
GET    /api/v1/requests/{id}/events   audit trail
GET    /api/v1/stats/today
GET    /api/v1/stats/export.csv
GET    /api/v1/pos                    list counters            manager
POST   /api/v1/pos/add                add the next counter     manager
POST   /api/v1/pos/remove             switch off the last       manager
PATCH  /api/v1/staff/{id}             rename a role            manager
GET    /api/network                   addresses (no auth, for the setup page)
GET    /setup/qr.png?u=               QR image (no auth)
GET    /api/health                    no auth
```

---

## Behaviour worth knowing

**Reference number** — `#001`, `#002`, … reset daily, allocated from `MAX(seq)+1`
rather than `COUNT`, so cancelling does not recycle a number onto a second SO.

**Barcode** — format `MACSO26-00142463`, matched against `^MACSO\d{2}-\d{8}$`.
Validation is **soft**: an unexpected format is accepted and flagged yellow, never
rejected — hard-rejecting would stall a queue over a mis-scan when the customer is
standing there. Values are upper-cased and whitespace-stripped server-side, because
`macso26-…` and `MACSO26-…` would otherwise become two different records.

**Dedupe, three layers** — same SO still open returns the existing row; same SO
finished within `QMS_DEDUPE_MIN` is suppressed as an accidental re-scan; anything
else creates a new job.

**Copy the SO number** — every card has a COPY button, because staff read the SO
number out loud to call the customer. It is deliberately layered: `navigator.clipboard`
in a secure context, falling back to a hidden textarea + `execCommand("copy")` over
plain HTTP, because POS screens open this on the store LAN where
`navigator.clipboard` does not exist at all. Tested both ways — the copy lands in the
clipboard on `http://192.168.x.x`.

**Ordering** — work lists are pure FIFO on the sequence. With one backstore person,
"who has waited longest" is the only ordering that matters, and a red flag is always
on one of the oldest cards anyway.

**Audit** — every transition lands in `so_events` with actor and timestamp. That is
the answer when someone says "I did that already".

---

## Development

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/uvicorn app:app --host 0.0.0.0 --port 8099
```

```bash
./venv/bin/python seed_demo.py                              # reset + demo state
./venv/bin/python test_flow.py                              # 30 checks, incl. concurrency
./venv/bin/python test_flow.py http://host:port             # against any instance
./venv/bin/python make_test_barcodes.py                     # printable test barcode sheet
./venv/bin/python make_qr_card.py                           # printable A4 wall sign
./venv/bin/python make_preview.py                           # stitch screenshots
```

CI (`.github/workflows/ci.yml`) runs the suite and a clean-database boot smoke test
on every push.

> Reaching a phone over HTTPS while developing:
> `cloudflared tunnel --config /dev/null --url http://localhost:8099`
> The `--config /dev/null` matters — see the pitfalls below.

---

## Pitfalls

- **`cloudflared` reads `~/.cloudflared/config.yml` even in quick-tunnel mode.** If
  a named tunnel config exists with a catch-all `http_status:404`, every request to
  your quick tunnel returns 404 and the app looks broken when it is fine. Always
  pass `--config /dev/null`. Diagnose from the tunnel side, not the app:
  `curl -s 127.0.0.1:20242/metrics | grep tunnel_total_requests`.
- **Adding a hostname to someone's existing named tunnel restarts cloudflared** and
  briefly drops every other service on it. Ask first.
- **iOS Safari does not support `BarcodeDetector`** (disabled through iOS 26.5).
  Use ZXing; the bundled copy is at `static/vendor/zxing.min.js`.
- **1D barcodes are unreliable on an iPhone camera.** Manual Entry is not optional.
- **Bump `CACHE` in `service-worker.js` on every frontend change**, or a phone that
  already loaded the app keeps serving the old JavaScript.
- **Do not fetch a narrow list for a screen that must show completions.** If the API
  returns only open items, a card vanishes the moment the other role finishes it and
  the person waiting sees nothing. Fetch the day and filter client-side.
- **Operational toasts need a long duration.** The 3-second default is useless when
  the user is mid-transaction with a customer.
- **Never put an unquoted value with spaces in `qms.env`.** `QMS_STORE_NAME=Test Outlet`
  breaks on `source` and in systemd's `EnvironmentFile`. Quote it.
- **`document.execCommand("copy")` needs a real user gesture.** A synthetic
  `element.click()` from an automated test returns `false` and makes the clipboard
  fallback look broken when it works fine under a real tap. Drive it with a trusted
  mouse event (CDP `Input.dispatchMouseEvent`) before believing a copy button is
  broken.
