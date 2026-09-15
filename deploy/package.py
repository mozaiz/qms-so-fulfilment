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

FILES = [
    "app.py", "run.sh", "install.sh", "requirements.txt",
    "README.md", "INSTALL.md", "LICENSE", "qms.env.example",
]
DIRS = ["static", "deploy"]
SKIP_DIRS = {"__pycache__", ".git", "venv", ".venv", "screens", "dist"}
SKIP_EXT = {".pyc", ".db", ".db-wal", ".db-shm", ".bak", ".log"}


def collect(name: str):
    """[(absolute_path, archive_relative_path)] for everything shipping."""
    out = []
    for f in FILES:
        p = os.path.join(ROOT, f)
        if not os.path.isfile(p):
            raise SystemExit(f"missing required file: {f}")
        out.append((p, f"{name}/{f}"))
    for d in DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            raise SystemExit(f"missing required directory: {d}")
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS]
            for fn in sorted(filenames):
                if os.path.splitext(fn)[1] in SKIP_EXT:
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
                out.append((full, f"{name}/{rel}"))
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
