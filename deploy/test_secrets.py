"""Refuse to ship a secret.

A public repository is forever. A token removed in the next commit is still
readable in the history by anyone who thinks to look, and by every scraper that
already looked. The only reliable moment to catch one is before it is pushed.

This scans every blob in every commit, not just the working tree, because the
working tree is the one place a leak is guaranteed not to be.

    ./venv/bin/python deploy/test_secrets.py

Exits non-zero on a finding, so CI stops the push.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

PASS, FAIL = [], []

# Shapes that are a credential and nothing else. Kept deliberately specific:
# a pattern that also fires on documentation is a pattern people learn to ignore,
# and an ignored check protects nothing.
PATTERNS = [
    ("GitHub token", r"gh[pousr]_[A-Za-z0-9]{20,}"),
    ("GitHub fine-grained token", r"github_pat_[A-Za-z0-9_]{20,}"),
    ("OpenAI/Anthropic key", r"sk-[A-Za-z0-9_-]{20,}"),
    ("AWS access key", r"AKIA[0-9A-Z]{16}"),
    ("Slack token", r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    ("Google API key", r"AIza[0-9A-Za-z_-]{30,}"),
    ("Tailscale key", r"tskey-[A-Za-z0-9]{10,}"),
    ("Telegram bot token", r"[0-9]{8,10}:AA[A-Za-z0-9_-]{30,}"),
    ("private key block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # No Cloudflare/Stripe/generic entry on purpose. A pattern for "40 random
    # characters" matches minified JS, hashes and base64, and a check that cries
    # wolf is a check somebody eventually disables. Named-vendor shapes only.
]

# Assignment of a literal to something credential-shaped. `password=None` and
# `token = secrets.token_hex(32)` are not secrets; a quoted value is.
ASSIGN = re.compile(
    r"""(?i)\b(api[_-]?key|secret|passwd|password|token|credential|bearer)\b"""
    r"""\s*[:=]\s*["'][^"']{12,}["']""")

# Files that legitimately contain credential-shaped text.
ALLOW_FILES = {
    "deploy/test_secrets.py",   # this file
}

# Placeholders, not values.
BENIGN = re.compile(r"(?i)(your|example|placeholder|xxx|redacted|dummy|change|TODO|<|\{\{)")

# Things that must never be committed at all.
FORBIDDEN_PATHS = [
    (re.compile(r"(^|/)certs?/"), "certificate authority material"),
    (re.compile(r"\.key$"), "a private key file"),
    (re.compile(r"\.pem$"), "a PEM key"),
    (re.compile(r"\.mobileconfig$"), "a generated profile"),
    (re.compile(r"(^|/)\.env"), "an environment file"),
    (re.compile(r"qms\.db"), "the store database"),
]


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  -- {detail}" if detail else ""))


def git(*args, cwd=REPO):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


print("=" * 64)
print("Secret scan — every blob in every commit, plus the working tree")
print("=" * 64)

# --------------------------------------------------------------- history
print("\n== git history ==")
print("  (a secret deleted in a later commit is still here)")

shallow = os.path.exists(os.path.join(REPO, ".git", "shallow"))
if shallow:
    print("  NOTE: shallow clone — history is incomplete, so this check is weaker.")
    check("history is complete enough to scan", False, "shallow clone: fetch-depth 0 needed")

objects = git("rev-list", "--objects", "--all").splitlines()
blob_ids = sorted({line.split()[0] for line in objects if line.strip()})
check("there is history to scan", len(blob_ids) > 0, f"{len(blob_ids)} objects")

findings = []
scanned = 0
for oid in blob_ids:
    if git("cat-file", "-t", oid).strip() != "blob":
        continue
    scanned += 1
    try:
        body = subprocess.run(["git", "cat-file", "-p", oid], cwd=REPO,
                              capture_output=True, timeout=20).stdout
    except subprocess.SubprocessError:
        continue
    try:
        text = body.decode("utf-8", "ignore")
    except Exception:
        continue
    for label, pat in PATTERNS:
        if re.search(pat, text):
            # allow the patterns that are obviously documentation
            for line in text.splitlines():
                if re.search(pat, line) and not BENIGN.search(line):
                    findings.append((oid[:10], label, line.strip()[:70]))
print(f"  blobs scanned: {scanned}")

check("no credential-shaped strings in any commit", not findings,
      "" if not findings else f"{len(findings)} finding(s): " + "; ".join(
          f"{o} {l}" for o, l, _ in findings[:4]))
for o, l, line in findings[:8]:
    print(f"        {o}  {l}: {line}")

# ------------------------------------------------------- forbidden paths
print("\n== nothing that must never be committed ==")
tracked = git("ls-files").splitlines()
for pat, why in FORBIDDEN_PATHS:
    bad = [f for f in tracked if pat.search(f) and f not in ALLOW_FILES]
    check(f"not tracked: {why}", not bad, ", ".join(bad[:4]))

ever = set()
for line in git("log", "--all", "--name-only", "--pretty=format:").splitlines():
    if line.strip():
        ever.add(line.strip())
for pat, why in FORBIDDEN_PATHS:
    bad = [f for f in ever if pat.search(f) and f not in ALLOW_FILES]
    check(f"never committed: {why}", not bad, ", ".join(bad[:4]))

# ------------------------------------------------------- working tree
print("\n== the working tree ==")
wt_findings = []
for root, dirs, files in os.walk(REPO):
    dirs[:] = [d for d in dirs if d not in
               {".git", "venv", ".venv", "__pycache__", "node_modules", "screens", "dist"}]
    for fn in files:
        rel = os.path.relpath(os.path.join(root, fn), REPO).replace(os.sep, "/")
        if rel in ALLOW_FILES or fn.endswith((".png", ".ico", ".pdf", ".zip", ".gz")):
            continue
        # A file git ignores cannot be committed, so it cannot leak. The store's
        # own CA key MUST exist on the box for HTTPS to work — that is the
        # design, not a finding.
        if subprocess.run(["git", "check-ignore", "-q", rel], cwd=REPO,
                          capture_output=True).returncode == 0:
            continue
        try:
            text = open(os.path.join(root, fn), encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for label, pat in PATTERNS:
            if re.search(pat, text):
                for line in text.splitlines():
                    if re.search(pat, line) and not BENIGN.search(line):
                        wt_findings.append((rel, label))
        for m in ASSIGN.finditer(text):
            if not BENIGN.search(m.group(0)):
                wt_findings.append((rel, f"literal assigned to {m.group(1)}"))
check("no secrets in the working tree", not wt_findings,
      "" if not wt_findings else f"{len(wt_findings)} finding(s)")
for rel, label in wt_findings[:8]:
    print(f"        {rel}: {label}")

# ------------------------------------------------------------- gitignore
print("\n== the things that must stay ignored ARE ignored ==")
gi = os.path.join(REPO, ".gitignore")
gitignore = open(gi, encoding="utf-8").read() if os.path.exists(gi) else ""
check(".gitignore exists", bool(gitignore))
for pattern, why in [
    ("certs/", "the certificate authority — its key can sign a certificate for ANY hostname"),
    ("qms.env", "store settings, which may hold local overrides"),
    ("*.db", "the store database"),
]:
    check(f"{pattern} is ignored ({why[:38]})", pattern in gitignore)

probe = tempfile.mkdtemp(prefix="qms-secrets-")
os.makedirs(os.path.join(probe, "certs"), exist_ok=True)
open(os.path.join(probe, "certs", "ca.key"), "w").write("x")
for probe_path, expect in [("certs/ca.key", True), ("qms.env", True),
                           ("qms.db", True), ("app.py", False)]:
    r = subprocess.run(["git", "check-ignore", "-q", probe_path], cwd=REPO)
    check(f"   ... enforced for {probe_path}", (r.returncode == 0) == expect,
          "ignored" if r.returncode == 0 else "NOT ignored")
shutil.rmtree(probe, ignore_errors=True)

print("\n" + "=" * 64)
print(f"PASSED {len(PASS)} / {len(PASS) + len(FAIL)}")
print("ALL GREEN" if not FAIL else "FAILURES: " + ", ".join(FAIL))
print("=" * 64)
sys.exit(0 if not FAIL else 1)
