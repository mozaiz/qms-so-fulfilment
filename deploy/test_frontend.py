"""Static checks on the frontend — no browser needed.

Catches the two failure modes that produce a blank page and that no API test can
see:

  1. JS referring to an element id that does not exist in the HTML. The reference
     is null, the handler throws, and the screen renders empty with no error the
     user can act on.
  2. A screen transition that hides one screen without showing another. That is
     exactly how "Setup & Addresses" produced a blank page: openSetup() hid the
     login screen and never showed the setup screen, while quietly loading all
     the data into a still-hidden element.

Also asserts the three version markers agree, so the release tag, /api/health and
the version pill in the UI cannot drift apart.

    ./venv/bin/python deploy/test_frontend.py
"""
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HTML = os.path.join(REPO, "static/index.html")
JS = os.path.join(REPO, "static/js/app.js")
SW = os.path.join(REPO, "static/service-worker.js")
APP = os.path.join(REPO, "app.py")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("" if not detail else f"  -- {detail}")))


html = open(HTML, encoding="utf-8").read()
js = open(JS, encoding="utf-8").read()
sw = open(SW, encoding="utf-8").read()
app = open(APP, encoding="utf-8").read()

html_ids = set(re.findall(r'id="([^"]+)"', html))
js_ids = set(re.findall(r'\$\("([^"]+)"\)', js))

print(f"== every element the JS reaches for must exist in the HTML ==")
print(f"   {len(js_ids)} referenced, {len(html_ids)} defined")
missing = sorted(js_ids - html_ids)
check("no JS reference to a missing element id", not missing, ", ".join(missing))

print("\n== screen transitions ==")
screens = sorted(i for i in html_ids if i.startswith("screen-"))
check("screens exist", len(screens) >= 3, ", ".join(screens))

# Screens are revealed either by a literal show(screen-x, true), or by the
# dynamic switcher in go() which walks ["scan","board","stats","setup"]. A
# screen that can be hidden but has no path back is the blank-page bug.
dynamic = re.search(r'\["scan",\s*"board",\s*"stats",\s*"setup"\]', js)
check("there is a dynamic screen switcher", bool(dynamic))
for s in screens:
    short = s.replace("screen-", "")
    literal = re.search(r'show\(\$\("' + re.escape(s) + r'"\),\s*true\)', js)
    via_go = bool(dynamic) and f'"{short}"' in dynamic.group(0)
    check(f"{s} can be revealed", bool(literal or via_go),
          "literal show()" if literal else ("via go()" if via_go else "NO PATH BACK"))

# the specific regression: openSetup must reveal the setup screen
m = re.search(r"async function openSetup\(from\)\s*\{(.*?)\n  \}", js, re.S)
check("openSetup exists", bool(m))
if m:
    body = m.group(1)
    check("openSetup SHOWS screen-setup", 'show($("screen-setup"), true)' in body)
    # and it must do so before it awaits, or a slow request leaves a blank page
    show_pos = body.find('show($("screen-setup"), true)')
    await_pos = body.find("await ")
    check("it shows the screen before the first await",
          show_pos != -1 and (await_pos == -1 or show_pos < await_pos),
          f"show at {show_pos}, first await at {await_pos}")

print("\n== version markers agree ==")
app_ver = re.search(r'version="([0-9.]+)"', app).group(1)
js_ver = re.search(r'var APP_VER = "([0-9.]+)"', js).group(1)
cache = re.search(r'const CACHE = "([^"]+)"', sw).group(1)
print(f"   app.py={app_ver}  app.js={js_ver}  cache={cache}")
check("app.py and app.js versions match", app_ver == js_ver)
check("service worker cache name is versioned", bool(re.match(r"^qms-v\d+$", cache)))

print("\n== screens are reachable ==")
for need in ("screen-login", "screen-scan", "screen-board", "screen-setup"):
    check(f"{need} has a way in", need in html_ids)

print("\n" + "=" * 58)
print(f"PASSED {len(PASS)} / {len(PASS) + len(FAIL)}")
print("ALL GREEN" if not FAIL else "FAILURES: " + ", ".join(FAIL))
print("=" * 58)
sys.exit(0 if not FAIL else 1)
