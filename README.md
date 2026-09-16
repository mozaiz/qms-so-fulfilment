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

Runs on **macOS** (any Mac, including the iMac at the counter), **Linux** (a mini
PC, a spare desktop, a Raspberry Pi) and **Windows**. One installer, one command,
the same on all of them — the platform is detected rather than chosen.

---

## Install

Open **Terminal** on the computer that will run it, and paste **one line**.

### macOS and Linux — install, or upgrade an existing install

```bash
curl -fsSL https://raw.githubusercontent.com/mozaiz/qms-so-fulfilment/main/install.sh | bash
```

That is the whole thing. It checks the machine, downloads the app, installs its
packages, registers it to start automatically, and prints the address to open.
About 2–4 minutes. **On a Mac it installs to `~/QMS`; on Linux to `/opt/qms`.**

When it says **Done**, open:

```
http://localhost:8099
```

### Check the machine first — changes nothing

```bash
curl -fsSL https://raw.githubusercontent.com/mozaiz/qms-so-fulfilment/main/install.sh | bash -s -- --check
```

```
==> Checking this computer

    macOS 14.5                         Intel (x86_64)
    Disk free                          178 GB
    Memory                             16 GB

    Python (needs 3.9 or newer)
      ✓ /usr/local/bin/python3         3.11.9   <- will use this
      ! /usr/bin/python3               3.9.6    venv support incomplete

    Port 8099                          free
    Existing install                   none — fresh install

    Result: READY
      Nothing needs installing on this computer.
      It will download the app, then about 25 MB of Python packages.
```

Nothing is downloaded, nothing is written, no service is touched. Safe on a
machine you have not decided about yet. If something is missing it says which
**one** command fixes it (`xcode-select --install` on a Mac, usually).

### Install a specific version instead of the latest

```bash
curl -fsSL https://raw.githubusercontent.com/mozaiz/qms-so-fulfilment/main/install.sh | QMS_REF=v0.6.4 bash
```

Use this for outlets: everyone gets the same tested build instead of whatever
`main` happens to be that morning.

### No internet at the outlet

Download `qms-<version>.tar.gz` from the **Releases** page, unzip it, and run:

```bash
bash install.sh
```

### Windows

Copy the folder and double-click `deploy\Install QMS (Windows).cmd`.

### What you need

| | |
|---|---|
| **A computer that stays on** during trading hours | Any Mac, a Linux mini PC, a spare desktop. 4 GB RAM is plenty. |
| **On the store wifi/LAN** | so the POS screens and phones can reach it |
| **A stable address** | ask whoever manages the router to reserve the IP |

Full step-by-step for the person setting it up at the outlet:
**[INSTALL.md](INSTALL.md)**

Then print the wall sign — this is the entire staff training (one line):

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; "$D/venv/bin/python" "$D/make_qr_card.py"
```

To cut a release:

```bash
git tag v0.6.5 && git push origin v0.6.5
```

CI builds the archives, **installs one in a clean container and boots it** to prove
it works, then attaches `qms-<version>.zip`, `.tar.gz` and `SHA256SUMS.txt` to the
release.

Licence: MIT — see [LICENSE](LICENSE).

---

## Running it day to day

Nobody needs to remember `launchctl` or `systemctl`. Every command below is a
**single line you can paste straight into Terminal**.

These are installed to `~/QMS` on a Mac and `/opt/qms` on Linux, so the
one-liners that follow work out the location themselves.

### Restart the service

The one people need most. Use it after an upgrade, or when the page stops
responding.

**Any machine — macOS or Linux:**

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; sh "$D/qms.sh" restart
```

**Short form if you know where it is:**

```bash
sh ~/QMS/qms.sh restart
```

```bash
sudo systemctl restart qms
```

> **An upgrade is not live until you restart.** The server reads `app.py` when it
> starts, so new code on disk does nothing until the service comes back up.

### Stop, start, check

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; sh "$D/qms.sh" status
```

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; sh "$D/qms.sh" stop
```

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; sh "$D/qms.sh" start
```

| | |
|---|---|
| `status` | is it running, where is the data, when was the last backup |
| `stop` | take it down — **your data is not touched** |
| `start` | bring it back |
| `restart` | stop, then start |
| `url` | the address each device should open |
| `log` | follow the log live (Ctrl+C to stop watching) |
| `backup` | take a backup right now |
| `doctor` | something is wrong — collect everything at once |
| `uninstall` | remove the program, keep the data |

**Where the service lives**, if you would rather use the platform's own tools:

| | macOS | Linux |
|---|---|---|
| name | `com.machines.qms` | `qms` |
| check | `launchctl print gui/$(id -u)/com.machines.qms` | `systemctl status qms` |
| starts | at login | at boot |

```
==> QMS status
    Service        loaded
    Answering      yes — v0.5.0 on port 8099
    Data           ~/QMS/qms.db  (60K)
                   412 SOs in total — 37 on the most recent day (2026-09-15)
    Last backup    qms_2026-09-15_0330  (30 kept)

    Open on this computer : http://localhost:8099
    Open on other devices : http://192.168.0.31:8099
```

### Stopping and starting does not touch your data

The database is one file, `qms.db`. Stopping QMS closes it cleanly; starting it
opens the same file. **Nothing is cleared, reset or re-seeded on boot.** A
re-install over the top reuses it too, so an upgrade never costs the day's queue.

This is proved, not asserted. `deploy/test_persistence.py` writes real data,
then for each of *polite stop (SIGTERM) → start → **hard kill (SIGKILL, the
power-cut case)** → start → re-install over the top*, checks the SOs and the
audit trail are all still there. It also asserts `app.py` contains no destructive
SQL against the business tables, so a future edit cannot quietly add one.

### Removing it

```bash
./install.sh --uninstall          # remove the program. KEEP the data.
./install.sh --uninstall --purge  # remove the program AND delete the data.
```

```
==> Removing QMS
    ✓ the program has been removed

    KEPT, in ~/QMS:

      qms.db        your data — every SO ever scanned
      backups/      the nightly copies
      qms.env       the store settings

    Deleting a day of queue history by accident is unforgivable, so
    this was kept on purpose. Nothing is running any more.
```

`--uninstall` stops the service, removes it from startup, and deletes the program
and its virtual environment. It **deliberately keeps** the data, and says so.
`--purge` deletes everything with no undo — and it refuses to run unless
`--uninstall` is also present, so a stray `--purge` can never mean anything on
its own. Neither will touch a directory that is not a QMS install.

### When nothing works, run the doctor

```bash
sh qms.sh doctor
```

It prints the OS, the install folder, the port, which files are present, whether
the service is loaded, whether anything is listening, whether it answers, and the
last 20 lines of the log — in one pasteable block. `qms.sh start` runs it
automatically if the server does not come up, and the installer runs it if the
first boot fails. No guessing.

If there is **no `qms.log` at all**, the server process never ran — that is a
service problem, not an application problem, and the doctor says so.

### Version

The sign-in screen shows `App vX · Server vY`, and every top bar carries a small
`vX` pill. When a phone is still running an older build than the server, a red
bar appears across the top with a **RELOAD** button — a cached client otherwise
looks exactly like a broken fix.

---

## Troubleshooting

Start here for anything. One line, works on any machine, changes nothing:

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; sh "$D/qms.sh" doctor
```

It prints the system, the install folder, the port, which files exist, whether
the service is loaded, whether anything is listening, whether it answers, and the
last 20 lines of the log — in one block you can copy and send to someone.

Then find your symptom:

### Nothing opens at all, or the page never loads

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; sh "$D/qms.sh" restart
```

Still nothing? Read the doctor output above. The decisive line is:

- **`no qms.log`** → the server process has never run. A **service** problem, not
  an app problem. Run `start` and read what it says.
- **`NOTHING is listening on 8099`** → nothing is bound to the port. Same as above.
- **something is listening but it does not answer** → the app started and then
  failed. The log below it says why.

### "Answering: no", or the service will not come up

Get the actual error instead of guessing:

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; tail -40 "$D/qms.log"
```

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; tail -40 "$D/qms.err.log"
```

### The page opens but looks wrong, or stubbornly shows an old version

This is a **cached client**, and it has caused more false bug reports than
anything else. The server is almost certainly fine.

- If there is a **red bar across the top**, tap **RELOAD**.
- If not, force the browser to forget the app:

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; sh "$D/qms.sh" url
```

Open that address, then use **AA → Website Settings → Clear Data** (Safari) or
**Settings → Privacy → Clear browsing data** (Chrome), and reload.

Check what the *server* is actually serving, which is the ground truth:

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; curl -s "http://127.0.0.1:8099/api/health"
```

If that says a newer version than the screen does, it is the client. Nothing is
broken on the server.

### The phone still cannot use the camera after installing the certificate

Work down this list — each one is common and each has a different fix.

**1. Are you on the `https://` address?** The certificate does not change anything
on `http://192.168.x.x`; that address can never have a camera. Open
`/setup/phone` again and use the secure address it gives you.

**2. iPhone: is Full Trust switched on?** Installing the profile is only half of
it. Check **Settings → General → About → Certificate Trust Settings** and make
sure the switch next to **QMS Store Certificate Authority** is green. This is the
single most common cause.

**3. Is the phone on the store wifi?** A phone on mobile data cannot reach
`192.168.x.x` at all.

**4. Did the store box change address?** Then the certificate no longer matches:

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; cd "$D" && ./venv/bin/python make_cert.py && sh qms.sh restart
```

**5. Is the secure port answering at all?**

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; curl -k -s -o /dev/null -w "%{http_code}\n" "https://127.0.0.1:8443/api/health"
```

`200` means the server is fine and the problem is the phone. `000` means the
server — read the doctor output.

**6. Android only:** skip the certificate entirely. Chrome →
`chrome://flags` → `unsafely-treat-insecure-origin-as-secure` → paste the **http**
address → relaunch.

### "Camera unavailable" or the camera does nothing

The browser only hands over a camera on a **secure context**.

| Where the page is opened | Camera |
|---|---|
| `http://localhost:8099` on the QMS computer | **works** |
| `http://192.168.x.x:8099` from a phone | blocked |
| `https://…` | works |

So the **SCANNER** role belongs on the QMS computer itself. POS and BACKSTORE
never need the camera and work fine on any phone. If a phone must scan, use
**Type Manually**, or put HTTPS in front (a Cloudflare Tunnel is free).

### Other devices cannot reach it

On a **Mac**, macOS asks two things the first time and both must be allowed:

1. *"Do you want the application python to accept incoming network connections?"*
2. *"…allow to find devices on local networks"*

Dismissed them? **System Settings → Network → Firewall → Options** → allow
`python`. On Linux, check the firewall on port 8099.

Then confirm the machine's own address:

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; sh "$D/qms.sh" url
```

### The installer stops, or says Python is missing

Run the check and read the `Result:` line:

```bash
curl -fsSL https://raw.githubusercontent.com/mozaiz/qms-so-fulfilment/main/install.sh | bash -s -- --check
```

- **`ONE STEP NEEDED`** → it prints the exact command. On a Mac that is almost
  always `xcode-select --install` — click **Install**, wait, then re-run.
- **`this build of QMS cannot start on this Python`** → the app was installed but
  failed to import. The traceback is above that line and in
  `"$D/import-check.err"`. Send it on.

### The port is already in use

The installer notices and steps to the next free port, then prints it. To see the
port actually in use:

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; grep QMS_PORT "$D/qms.env"
```

### I cannot reach the app on the address I wrote down

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; sh "$D/qms.sh" url
```

Or open the **Setup & Addresses** link on the sign-in screen — it shows every
address this machine answers on, with QR codes to scan.

### Where is my data, and how do I back it up?

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; sh "$D/qms.sh" status
```

Everything is one file, `qms.db`, in the install folder. Nightly backups land in
`backups/` next to it (30 kept). Take one right now:

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; sh "$D/qms.sh" backup
```

### Everything else

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; sh "$D/qms.sh" doctor
```

Send that whole block on. It answers most questions before they are asked.

---

## Access, and the camera catch

### Why `localhost` works on the QMS computer and nowhere else

`localhost` is not an address on your network. It means **"this device, itself"**.
Every device has its own, and they never cross the wifi.

So when a phone opens `http://localhost:8099` it is asking *the phone's own port
8099* — where nothing is running. The server is on the other machine. Nothing is
broken and nothing is misconfigured; there is simply nothing listening on the
phone.

Other devices must ask for the **QMS computer**, by an address that actually routes:

```
On the QMS computer   http://localhost:8099        <- its own loopback
On any other device   http://192.168.x.x:8099      <- over the wifi
```

QMS already binds to all interfaces, so LAN access works out of the box — no
setting to change. The **Setup & Addresses** screen lists every address the
machine answers on, each with its own QR code, so nobody has to type an IP.

> On **iOS**, `localhost` will fail exactly the same way — it is not an Android
> versus iOS difference. If a phone cannot reach the app by **LAN address**
> either, check **iOS Settings → Privacy → Local Network** (Safari needs it), and
> turn off iCloud Private Relay for that wifi.

### Scanning with a phone camera — how it actually works

Browsers only hand over a camera on a **secure context**. `localhost` counts as
secure; a LAN IP over plain HTTP does not. Measured in a real browser against a
real QMS:

```
A  http://192.168.0.25:8099       isSecureContext False   mediaDevices missing
B  https://… (untrusted cert)     isSecureContext False   mediaDevices missing
C  https://… (store CA installed) isSecureContext True    mediaDevices present
```

Row **B** is the one that catches people out. Tapping through a certificate
warning does **not** help — and on **iOS it never will**, because Safari does not
expose `navigator.mediaDevices` at all on a page whose certificate it does not
fully trust. That is a deliberate Apple security decision, not a bug to work
around. The phone has to genuinely trust the server.

So QMS runs its **own tiny certificate authority** — created by the installer,
kept on the store box, never sent anywhere.

### Setting up a phone — one minute, once per phone

Send staff to this address **on the phone** (plain HTTP is fine to start — this is
the one page that must work before the phone trusts anything):

```
http://<this-machine-ip>:8099/setup/phone
```

The page detects the phone and walks through it, including the step everyone
skips:

| | |
|---|---|
| **iPhone / iPad** | Install the profile → **Settings → General → About → Certificate Trust Settings → switch it ON**. Installing alone is not enough; Apple requires the second switch. |
| **Android** | Download the certificate → open it → name it `QMS` → choose **CA certificate** → confirm your screen lock. |
| **Android, no certificate** | In Chrome open `chrome://flags`, search `unsafely-treat-insecure-origin-as-secure`, paste the **http** address, relaunch. This skips the whole certificate route. |

Then the scanner uses the secure address:

```
https://<this-machine-ip>:8443
```

`Setup & Addresses` on the sign-in screen shows this address with its own QR code,
so nobody has to type it.

**Both listeners run at once, from one service.** The plain port is unchanged and
is where **POS** and **BACKSTORE** belong — they never need a camera and never
need to install anything. Only the **SCANNER** uses the secure port. If the secure
port is busy, QMS says so and serves the plain port anyway rather than taking the
whole store down.

### If you would rather not install anything on the phones

**A USB barcode scanner.** It behaves as a keyboard: it types the SO number and
presses Enter. Plain HTTP, no camera, no certificate, no internet, roughly
RM50–150 once. QMS is built for it — on a non-HTTPS page it **opens and focuses
the scan box by itself** and **re-focuses after every scan**, so a full day of
scanning needs no screen taps. Verified by driving the field key-by-key:

```
after sign-in    manualOpen: True   focused: 'manualCode'   (no taps needed)
shot 1           SCANNED ✓ #001 MACSO26-00142463
shot 2           SCANNED ✓ #002 MACSO26-00142464   (still armed)
```

Also still there: the camera on the **store computer itself**
(`http://localhost:8099` is already a secure context), and typing the SO number.

### If the store's address changes

The certificate names every address the machine answers on, and `make_cert.py`
rebuilds it when a new one appears — while keeping the **same** CA, so phones stay
trusted. If a phone suddenly shows a certificate error, the box moved address:

```bash
D=/opt/qms; [ -d "$D" ] || D="$HOME/QMS"; cd "$D" && ./venv/bin/python make_cert.py
```

Then restart. Re-installing the profile on the phones is **not** needed.

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

`scanned` → `assigned` → `delivered` → `completed`, or `cancelled`

Shown to staff as **WAITING / PENDING STOCK / DELIVERED / COMPLETED / CANCELLED**.

`assigned` is *pending stock* — the counter has it but the item has not arrived.
`delivered` is *at the counter* — the item is physically there and the POS has not
closed the entry yet. `completed` is the only terminal state, and the only one
that takes an SO out of the queue.

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

Four things turn a card **red** — flagged, never auto-actioned:

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
| `QMS_STORE_CODE` | *(blank)* | Store code, shown on the QR sign. Set per outlet |
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
./venv/bin/python test_flow.py                              # 50 checks, incl. concurrency
./venv/bin/python test_flow.py http://host:port             # against any instance
./venv/bin/python make_test_barcodes.py                     # printable test barcode sheet
./venv/bin/python make_qr_card.py                           # printable A4 wall sign
./venv/bin/python make_preview.py                           # stitch screenshots
```

### Test suites

| Suite | Checks | What it protects |
|---|---|---|
| `test_flow.py` | 50 | The API, the four stages, the FIFO ordering, and **four simultaneous claims on one SO resolving to exactly one winner** |
| `deploy/test_install_linux.sh` | 21 | The installer on Linux, a real install, then the full API suite against the installed copy; preflight in all four states |
| `deploy/test_install_macos.sh` | 30 | The macOS branch with `uname`/`launchctl`/`ipconfig`/`caffeinate` stubbed, the generated plists validated with `plistlib`, and the backup run for real |
| `deploy/test_persistence.py` | 8 | That stopping, restarting, hard-killing and re-installing **never lose the day's data** |
| `deploy/test_https.py` | 34 | **The phone-scanner path**: the local CA, that the certificate covers every address the box answers on, that it rebuilds when the address moves but keeps the same CA (or every phone in the store silently loses trust), that HTTPS is trusted with the CA and **rejected** without it, and that the iOS profile is a real profile |
| `deploy/test_readme.py` | 25 | **Every copy-paste one-liner in this README, executed** against a real install — a README nobody has run is worse than none |
| `deploy/test_lifecycle.py` | 47 | install → uninstall → **re-install with the data intact** → purge, that a bare `--purge` refuses, and that `qms.sh doctor` correctly reports a stopped install and a running one |

The installer tests install into a throwaway directory on a free port and run the
real API suite against what they installed — an installer verified by reading it
is not verified. CI runs all of them on every push, plus a clean-database boot
smoke test.

The macOS suite runs on Linux by stubbing the platform binaries, so the parts
that break in practice — interpreter discovery, plist generation, path quoting —
are covered without a Mac. It deliberately installs into a path **containing a
space**, because macOS home directories often do.

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
- **`from __future__ import annotations` does not protect Pydantic on Python 3.9.**
  Pydantic *evaluates* model fields, so `pos_number: int | None` raises
  `TypeError: Unable to evaluate type annotation 'int | None'` at import on 3.9 —
  while remaining perfectly valid on 3.10+. Use `Optional[int]`. This matters
  here more than usual because **macOS ships Python 3.9.6** with the Command Line
  Tools, so it is the default interpreter on any Mac without Homebrew.
- **An installer must prove the app starts, not that the packages installed.**
  `pip install` succeeding says nothing about whether the code runs. The
  installer now does `./venv/bin/python -c "import app; assert app.app"` before
  printing its summary, and refuses to claim success if that fails. That one line
  catches annotation incompatibilities, missing imports and bad dependency
  versions, on every platform.
- **Probe `ensurepip`, not `venv`.** On Debian and Ubuntu `import venv` succeeds
  while `ensurepip` is missing, so a version-only check picks a Python that then
  dies during `python -m venv` with a message pointing at the wrong fix. Select on
  version **and** `ensurepip`.
- **`set -e` plus a failing command substitution kills an installer silently.**
  `VAR="$(some_command)"` inherits the exit status, so one failing `df` aborts the
  script with no output at all. Every substitution used in an assignment ends in
  `|| true`.
- **BSD and GNU userland differ where you cannot debug it.** `xargs -r`,
  `readlink -f`, `hostname -I`, `sed -i` and `stat -c` are GNU-only;
  `df -g` is the macOS spelling; macOS `/bin/bash` is 3.2, so no `${v,,}` or
  `mapfile`. `readlink` without `-f` and `/dev/tcp` are fine on both.
- **Do not depend on a CLI that might not be installed.** The backup job used
  `/usr/bin/sqlite3` — always present on macOS, often missing on Linux, so the
  backup silently produced nothing. It uses the venv's Python and
  `sqlite3.Connection.backup()` instead, which is the correct online-backup API
  anyway.
- **A macOS LaunchAgent starts at login, not at boot.** Keep automatic login on
  for a POS Mac, and wrap the server in `caffeinate -disu` so it does not
  idle-sleep and drop every other counter off the LAN.
- **Do not use `cron` on macOS** for the nightly backup: it needs Full Disk
  Access and silently does nothing without it. Use a second LaunchAgent with
  `StartCalendarInterval`.
