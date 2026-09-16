"""Build the outlet-ready archives.

    python3 deploy/package.py v0.3.0

Writes qms-<version>.zip and .tar.gz plus SHA256SUMS.txt into the repo root,
and prints what went in. Uses only the standard library — no `zip` binary, no
extra tools — so it behaves the same on a laptop and on a CI runner.

Deliberately excluded: the database, the virtualenv, generated images and the
dev-only generators. An outlet needs the app and the installers, nothing else.
"""
import hashlib
import os
import sys
import tarfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Ship everything in the repository except an explicit ignore list.
#
# This used to be an allowlist, and it went stale exactly as allowlists do: a new
# module was imported by app.py but never added here, so every published archive
# contained an app that could not boot. A deny-list fails the other way — a file
# someone forgot to exclude gets shipped, which is annoying but visible, whereas
# a missing file is an install that dies on the first run.
SKIP_FILES = {
    "qms.env",              # store settings, may hold local overrides
    ".gitignore", "SHA256SUMS.txt",
}
SKIP_DIRS = {
    "__pycache__", ".git", ".github", "venv", ".venv", "screens", "dist",
    "certs",                # the private CA key must never leave the store box
    "backups", ".pytest_cache", ".mypy_cache", "node_modules",
}
SKIP_EXT = {
    ".pyc", ".db", ".db-wal", ".db-shm", ".bak", ".log", ".tgz", ".zip",
    ".mobileconfig",        # generated per store
    ".key", ".pem",         # key material, whatever it is called
}
# os.path.splitext("qms-1.0.tar.gz") is ".gz", so multi-part extensions and
# generated output need matching by name instead.
SKIP_SUFFIX = (".tar.gz", ".tgz", ".zip", "~")
SKIP_PREFIX = ("qr_card_",)
SKIP_NAME = {"test_barcodes.png"}

# These are not preferences. Without them the archive boots into a broken app,
# which is precisely the failure this list exists to make impossible.
REQUIRED = [
    "app.py", "netinfo.py", "make_cert.py",
    "run.sh", "install.sh", "requirements.txt",
    "static/index.html", "static/js/app.js", "static/phone-setup.html",
]


def collect(name: str):
    """[(absolute_path, archive_relative_path)] for everything shipping."""
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            if (fn in SKIP_FILES or fn in SKIP_NAME
                    or fn.startswith(SKIP_PREFIX)
                    or fn.endswith(SKIP_SUFFIX)
                    or os.path.splitext(fn)[1] in SKIP_EXT):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
            out.append((full, f"{name}/{rel}"))

    have = {rel[len(name) + 1:] for _, rel in out}
    missing = [f for f in REQUIRED if f not in have]
    if missing:
        raise SystemExit(
            "these files are required for QMS to run and are not being packaged: "
            + ", ".join(missing))
    return out


def main() -> int:
    version = sys.argv[1] if len(sys.argv) > 1 else "dev"
    if version and not version.startswith("v") and version != "dev":
        version = "v" + version
    name = f"qms-{version}"

    items = collect(name)
    zip_path = os.path.join(ROOT, f"{name}.zip")
    tgz_path = os.path.join(ROOT, f"{name}.tar.gz")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for full, rel in items:
            z.write(full, rel)

    with tarfile.open(tgz_path, "w:gz") as t:
        for full, rel in items:
            t.add(full, arcname=rel)

    lines = []
    for path in (zip_path, tgz_path):
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        size = os.path.getsize(path)
        lines.append(f"{h.hexdigest()}  {os.path.basename(path)}")
        print(f"{os.path.basename(path):<32} {size/1024:8.1f} KB")

    sums = os.path.join(ROOT, "SHA256SUMS.txt")
    with open(sums, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"\n{len(items)} files packaged into {name}")
    for _, rel in items:
        print("  " + rel.replace(name + "/", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
