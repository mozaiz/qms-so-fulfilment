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

### Path B — mini PC / Linux  *(tested)*

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
| **POS 1 – 4** | Any phone/tablet/POS screen on the wifi | Work the queue: claim an SO, complete it when the customer has it |
| **BACKSTORE** | Any phone/tablet on the wifi | See "DELIVER TO POS 3", walk it over, tick it |

**The queue is the list you call customers from.** An SO stays in it — claimed or
not — until someone marks it complete. Use **COPY** on a card to copy the SO number
if you need to read it out or paste it somewhere.

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
