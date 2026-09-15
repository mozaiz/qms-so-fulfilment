# Installing QMS at an outlet

QMS is **one small program** that runs on **one computer inside the store**. Every
phone, tablet and POS screen in that store then just opens a web address. There is
no cloud account, no subscription, and no data leaves the building.

Budget **10 minutes** for the first outlet. After that, each extra outlet is a
copy of the same folder.

---

## What you need

| | |
|---|---|
| **A computer that stays on** during trading hours | Any Windows PC, Mac, or a mini PC (e.g. a used Dell Wyse 5070). 4 GB RAM is plenty. |
| **It should be on the store wifi/LAN** | So the POS screens and phones can reach it |
| **Its IP address should not change** | Ask whoever manages the router to reserve/static it (see *Keep the address stable* below) |

---

## Pick ONE path

### Path A — Windows store PC  *(most common)*

1. Copy the QMS folder onto the PC (e.g. to `C:\QMS`).
2. Open the `deploy` folder and double-click **`Install QMS (Windows).cmd`**.
3. Wait. It downloads Python if needed, installs everything, and makes a
   **QMS** shortcut on the Desktop.
4. From now on, staff just double-click **QMS** on the Desktop.

> To start it automatically every time the PC boots: press `Win+R`, type
> `shell:startup`, press Enter, and put a copy of the Desktop **QMS** shortcut in
> the folder that opens.

<sub>Windows path is written but not yet proven on a real store PC — if it errors,
use Path B on a mini PC, or the manual steps at the bottom.</sub>

### Path B — Mac  *(tested)*

Works on any Mac: MacBook Air, Mac mini, or the iMac at the POS counter.

Open **Terminal** (press `⌘ Space`, type `Terminal`, press Enter), then either:

**If you have the QMS folder already** — type `cd `, drag the folder into the
Terminal window, press Enter, then:

```bash
bash install.sh
```

**Or straight from the internet**, with nothing downloaded:

```bash
curl -fsSL https://raw.githubusercontent.com/mozaiz/qms-so-fulfilment/main/install.sh | bash
```

Either way it takes 2–4 minutes. When it prints **Done**, open
`http://localhost:8099`.

**Not sure if the Mac is up to it? Check first — this changes nothing:**

```bash
bash install.sh --check
```

It prints the macOS version, the chip, free disk, memory, every Python it can
find, whether the port is free, and whether an older install is already there —
then says either **READY** or **ONE STEP NEEDED** with the exact command that
fixes it. Nothing is downloaded, nothing is written, no service is touched, so
it is safe to run on a machine you have not decided about yet.

You do not have to run it separately — a normal install does the same checks
first and prints the same report before it touches anything.

What it sets up, so you know what to expect:

- Installs into **`~/QMS`** (your home folder — no `sudo`, no password needed).
- Registers a **launch agent**, so QMS starts by itself every time you log in
  and comes back if it ever crashes.
- Wraps the server in **`caffeinate`**, so the Mac does not fall asleep and drop
  the other counters off the wifi.
- Sets up a **nightly SQLite backup** at 3:30am into `~/QMS/backups`
  (last 30 kept). It uses `sqlite3 .backup`, not a file copy — a copy taken while
  the app is writing gives you a backup you cannot restore.

**Keep automatic login on** for a POS Mac. A launch agent starts at *login*, not
at boot, so the machine has to log itself in.

> **macOS will ask two questions the first time.** Both must be allowed:
>
> 1. *"Do you want the application **python** to accept incoming network
>    connections?"* → **Allow**. Without this, no other device can reach QMS.
> 2. *"…allow to find devices on local networks"* → **Allow**, same reason.
>
> If you dismissed them, open **System Settings → Network → Firewall → Options**
> and allow `python`.

### Path C — mini PC / Linux  *(tested)*

Plug in the box, open a terminal, and run:

```bash
curl -fsSL https://raw.githubusercontent.com/mozaiz/qms-so-fulfilment/main/install.sh | bash
```

Or, if you already copied the folder over, or downloaded an archive from the
[Releases page](https://github.com/mozaiz/qms-so-fulfilment/releases):

```bash
cd qms
./install.sh
```

It installs everything, starts QMS on boot, sets up a nightly local backup, and
prints the addresses at the end. Re-running it is safe.

To change the store name or port, edit `/opt/qms/qms.env` then
`sudo systemctl restart qms`.

### Path C — Docker

```bash
docker compose up -d
```

> Not tested in the environment this was built in (no Docker available there).

---

## Then: print the wall sign

On the QMS machine, run:

```bash
./venv/bin/python make_qr_card.py
```

That writes **`qr_card_<STORE>.pdf`** — an A4 sheet with a big QR code, the web
address in plain text, and three steps. **Print it and tape it next to the POS.**

This single sheet is the whole training. Staff scan the QR, tap their role, done.

---

## What staff actually do

| Role | Their device | What they do |
|---|---|---|
| **SCANNER** | The QMS computer itself | Scan the customer's SO barcode |
| **POS 1 – 4** | Any phone/tablet/POS screen on the wifi | Claim an SO from the Queue, then mark it complete once the customer has the item |
| **BACKSTORE** | Any phone/tablet on the wifi | See "DELIVER TO POS 3", walk it over, tick it delivered |

**What a POS sees, in order:**

1. **Queue** — SOs nobody has taken yet, oldest scan first
2. **My SOs** — what this counter has claimed. The badge reads **PENDING STOCK**
   while the backstore is fetching, then turns to **DELIVERED** once the item is
   physically at the counter
3. **Completed** — everything this counter has closed

**The customer is called by SO number**, so My SOs stays in the order the SOs were
scanned. Use **COPY** on a card to copy the SO number if you need to read it out or
paste it somewhere.

An SO stays in **My SOs** until it is marked complete — claiming it never makes it
disappear from the system.

### ⚠️ The one thing that trips people up

The **camera only works on the computer running QMS**, or over HTTPS.

Browsers only hand over a camera on a "secure" page. `http://localhost` counts as
secure — an address like `http://192.168.0.25` does **not**. So:

- **SCANNER** → use the QMS computer itself (`http://localhost:8099`). Camera works.
- **POS / BACKSTORE** → any device is fine. They don't use the camera.

QMS detects this and shows a clear yellow warning instead of silently failing.

If you need the camera on a phone too, put an HTTPS address in front of it (a
Cloudflare Tunnel is free) — see the README.

---

## Keep the address stable

The QR code and the wall sign are tied to the machine's IP. If the router hands
out a different IP tomorrow, the sign stops working.

Ask whoever manages the store router to **reserve the IP** for the QMS machine
(or set a static IP on the machine). Then re-print the sign if you ever change it.

---

## Adding more POS counters

Out of the box there are **4** (POS 1–4). To add more:

1. On the QMS computer open `http://localhost:8099`
2. Sign in as **⚙ Manager**
3. Go to the **Setup** tab → **+ Add POS**

**POS 5** appears on the sign-in screen immediately — on every device, no restart.
Adding again gives POS 6, and so on (up to 20). **− Remove last** switches the
newest counter off again.

---

## Adding a second, third, … outlet

Each outlet is completely independent — its own copy, its own database, its own
POS counters. Nothing to configure centrally.

1. Repeat the install on that outlet's machine
2. Set a different store code:
   - Linux: edit `QMS_STORE_CODE` in `/opt/qms/qms.env`, then
     `sudo systemctl restart qms`
   - Windows: edit `QMS_STORE_CODE` in `C:\QMS\qms.env`, then restart QMS
3. Print that outlet's own QR sign

---

## Backups

- **Linux:** a nightly copy lands in `/var/backups/qms`, kept 30 days.
- **Windows:** copy `C:\QMS\qms.db` somewhere safe now and then. It is one file.

The database is a single SQLite file. To back it up while QMS is running, copy it
with `sqlite3 qms.db ".backup out.db"` rather than a plain file copy.

---

## Manual install (no scripts)

```
1. Install Python 3.10 or newer
2. In the QMS folder:
       python -m venv venv
       venv/bin/pip install -r requirements.txt        (Windows: venv\Scripts\pip)
3. Copy qms.env.example to qms.env and edit it
4. Start it:
       venv/bin/uvicorn app:app --host 0.0.0.0 --port 8099
5. Open http://localhost:8099
```

---

## Day-to-day, and removing it again

Everything runs from the install folder. Nobody needs to remember `launchctl`
or `systemctl`:

```bash
sh qms.sh status      # is it running, where is the data, when was the last backup
sh qms.sh stop        # take it down
sh qms.sh start       # bring it back
sh qms.sh restart     # stop, then start
sh qms.sh url         # the address each device should open
sh qms.sh log         # follow the log
sh qms.sh backup      # take a backup right now
sh qms.sh doctor      # nothing works — dump everything worth knowing
```

Or from anywhere: `bash install.sh --status`

### Stopping and starting does not touch your data

The database is a single file, `qms.db`, in the install folder. Stopping QMS
closes it cleanly; starting it opens the same file. **Nothing is cleared, reset
or re-seeded on boot.** Re-installing over the top reuses it too, so an upgrade
never costs you the day's queue.

This is verified rather than assumed. `deploy/test_persistence.py` writes real
data, stops the service politely (SIGTERM), starts it again, kills it hard
(SIGKILL — the power-cut case), starts it again, and re-installs over the top —
checking the SOs and the audit trail survive every step.

### Removing it

```bash
bash install.sh --uninstall          # remove the program. KEEP the data.
bash install.sh --uninstall --purge  # remove the program AND delete the data.
```

`--uninstall` on its own stops the service, removes it from startup, and deletes
the program and its virtual environment. It **deliberately keeps** `qms.db`,
`backups/` and `qms.env`, and prints where they are. Deleting a day of queue
history by accident is not a mistake worth making easy.

`--purge` deletes everything and there is no undo. It refuses to run unless you
also pass `--uninstall`, so a stray `--purge` can never do anything on its own.

---

## Troubleshooting

**"Camera unavailable" / camera does nothing**
You are on an address that is not HTTPS. Open `http://localhost:8099` on the QMS
computer itself, or use **Manual Entry**.

**Other devices can't open the address**
The QMS computer and the device must be on the same wifi/LAN, and the computer's
firewall must allow the port. On Windows, allow Python through the firewall when
prompted.

**"Port already in use"**
Something else is on 8099. Change `QMS_PORT` in `qms.env` and restart. Remember to
re-print the QR sign afterwards.

**A POS tapped the same SO as another POS**
That is handled: exactly one wins, the other sees *"Already claimed by POS 3"*.
This is enforced by the database, not by hope.

**I need to see the log**
Linux: `journalctl -u qms -f` (add `--user` if installed as a user service).
Windows: the console window that QMS keeps open.
