# Security

QMS is an internal store tool. It is designed for a shop floor, and most of its
security comes from where it runs rather than from what it asks for. This page
states that plainly, because the failure mode is a deployment nobody thought
about — and the whole point is that a store can set it up without a security
team.

## The security model, in one paragraph

**QMS trusts the network it runs on.** It has no passwords, no PINs and no user
accounts to steal: staff tap their role to sign in. That is a deliberate choice
— the person setting it up has never used a terminal, and a queue system that
needs password resets does not survive contact with a busy Saturday. In exchange,
**anything that can reach the address can act as any role, including Manager.**

So the boundary is not the app. The boundary is: *who can reach the port.*

## Therefore

**Do not expose QMS to the internet.** A store runs it on `localhost` and on the
store wifi. Nothing else. If you put it on a public URL, anyone who finds that
URL can read every SO ever scanned, export the lot as CSV, add and remove POS
counters, and cancel queue entries. Not with a clever exploit — by tapping
"Manager".

If you need remote access, put something in front that authenticates: Cloudflare
Access (free tier, email one-time-PIN) or Tailscale, both of which keep the app
itself as dumb as it is.

**The demo is the exception that proves it.** `qms.mozaiz.my` is a live demo for
a manager, running on demo data, and it is deliberately open — which is exactly
why it must carry nothing real. It does not: the queue holds seeded rows, and the
staff are `Scanner`, `POS 1`…`Manager`. Keep it that way. **Never point the demo
at a store's database**, and take it down when it is not being shown.

## What is protected, and how

| | |
|---|---|
| **Who may do what** | Every endpoint except `/api/login`, `/api/network`, `/api/health` and `/setup/*` is behind a session token, and each action checks the role (`require_role`). A POS cannot mark another POS's SO complete; a scanner cannot complete one at all. |
| **Session tokens** | `secrets.token_hex(32)` — 256 bits from the OS CSPRNG. Guessing is not a threat. |
| **SQL injection** | Every value is a bound parameter. The `WHERE`/`ORDER BY` fragments are internal literals, never request data. Verified with an injection attempt that left the tables intact. |
| **XSS** | Everything rendered into the DOM goes through `esc()`. |
| **Cross-origin** | No CORS headers, so another website cannot read the API through a signed-in browser. |
| **Path traversal** | Static serving resolves only inside the app directory; `/../app.py` and friends return 404. |
| **The store's certificate authority** | `certs/` is gitignored — `ca.key` can sign a certificate for any hostname on earth. It lives on one store box at mode 0600 and is never published. `deploy/test_secrets.py` fails the build if that ever changes. |
| **Secrets in the repo** | `deploy/test_secrets.py` walks **every blob in every commit**, not just the working tree, because a token deleted in a later commit is still readable in the history. It runs in CI with `fetch-depth: 0`. |

## Known limitations, stated rather than hidden

**A session token never expires.** There is no expiry and no cleanup — a token is
valid until the browser logs out. On a trusted LAN with a physically controlled
box this is acceptable, and it means an all-day POS screen never gets logged out
mid-queue. It does mean a token captured once works forever; see the next point.

**POS and Backstore authenticate over plain `http://`.** The token travels in the
clear on the store wifi. Anyone able to read that traffic — a shared or guest
network, a compromised access point — can take a session and act as that role, or
as Manager if it was a Manager's session. This is the price of not making staff
install a certificate on every counter. The scanner is unaffected: it uses
`https://` and a certificate, because the camera forces it anyway.

If a store's wifi is not trusted, the fix is to use the `https://` address for
POS and Backstore too, and install the certificate on those devices as well.

**The person at the counter can act as Manager.** The Manager role is a radio
button on the sign-in screen. It is not a privilege boundary against staff — it
is a way to keep the wrong buttons off the wrong screen.

**`install.sh` fetches from `main` by default.** If the GitHub account were
compromised, every store re-running the installer would run new code. Pin it for
production:

```bash
curl -fsSL https://raw.githubusercontent.com/mozaiz/qms-so-fulfilment/main/install.sh | QMS_REF=v0.8.1 bash
```

## A startup race, and why the listeners are started in order

`run.sh` starts the plain listener first, waits for it to answer, and only then
starts the secure one. That ordering is deliberate.

On a first install both listeners create and migrate the same database. SQLite
cannot be made to wait for DDL or for a journal-mode switch the way `busy_timeout`
handles ordinary writes — `PRAGMA journal_mode=WAL` fails outright if another
connection is active, and that failure ignores the timeout entirely. Starting the
two together was therefore a race that one of them lost, and the only symptom at
a store is *"the scanner does not work"*, with the service apparently running.

The app also no longer dies for that: WAL is a persistent property of the database
rather than of a connection, so the switch is attempted once and tolerated, and
`init_db` retries a lock failure instead of giving up. Both are covered by
`deploy/test_persistence.py`.

## Reporting

This is a personal internal tool, not a product with a security team. Open an
issue — but if it is a real vulnerability rather than a design trade-off
documented above, say so in the title and keep the details out of the body until
it is fixed.
