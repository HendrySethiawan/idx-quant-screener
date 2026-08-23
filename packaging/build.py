#!/usr/bin/env python3
"""
Build the double-clickable Windows package.

    python packaging/build.py            # build, verify, zip
    python packaging/build.py --no-zip   # leave it as a folder

The verification step is the point of this script existing rather than a bare
`pyinstaller` command. `configs/user.yaml` holds the reader's capital and
`current_holdings.yaml` holds their positions; a build that swept `configs/*` into
the bundle would put a private number inside every copy of a binary that might be
handed to someone, and it would be invisible, because nobody unzips an exe to check.

So the finished tree is walked and the build is FAILED on a hit. A comment in the
spec is a promise; this is a check.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "idx_terminal.spec"
DIST = ROOT / "dist" / "IDX Terminal"
APP = "IDX Terminal"

# Files that must never end up inside a distributable build.
FORBIDDEN_NAMES = (
    "user.yaml",
    "events.yaml",          # the example is events.example.yaml, which is fine
    "current_holdings.yaml",
    "journal.csv",
    "journal_marks.csv",
)

# Directories whose contents are personal by definition.
FORBIDDEN_DIRS = ("data/cache", "data/output", "logs")


def _read_real_capital() -> str | None:
    """
    The capital figure from the local, git-ignored user.yaml -- so the scan can look
    for the actual number rather than a guess. Absent on a machine that has never
    set one, in which case that half of the check simply does not apply.
    """
    user = ROOT / "configs" / "user.yaml"
    if not user.exists():
        return None
    match = re.search(r"capital_rp:\s*([0-9_]+)", user.read_text(encoding="utf-8"))
    return match.group(1).replace("_", "") if match else None


def verify(tree: Path) -> list[str]:
    """Every reason this build must not be distributed. Empty list means clean."""
    problems: list[str] = []

    for path in tree.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(tree).as_posix()

        if path.name in FORBIDDEN_NAMES:
            problems.append(f"personal file bundled: {rel}")
        for bad in FORBIDDEN_DIRS:
            if rel.startswith(bad + "/"):
                problems.append(f"personal directory bundled: {rel}")

    capital = _read_real_capital()
    if capital:
        # Only text-ish files: the number could appear by coincidence in a DLL, and
        # a false alarm that blocks every build teaches people to ignore the check.
        for path in tree.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in (
                ".yaml", ".yml", ".json", ".txt", ".csv", ".html", ".md", ".py"
            ):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if re.search(rf"\b{capital}\b", text):
                problems.append(
                    f"the real capital figure appears in {path.relative_to(tree)}"
                )

    default_yaml = tree / "configs" / "default.yaml"
    if not default_yaml.exists():
        problems.append("configs/default.yaml is missing - the app cannot start")

    exe = tree / f"{APP}.exe"
    if not exe.exists():
        problems.append(f"{APP}.exe is missing")

    return problems


def build() -> None:
    for stale in (ROOT / "build", ROOT / "dist"):
        if stale.exists():
            shutil.rmtree(stale)

    print(f"Building {APP}...")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", str(SPEC)],
        cwd=ROOT,
    )
    if result.returncode != 0:
        raise SystemExit(f"PyInstaller failed with exit code {result.returncode}")


def stage() -> None:
    """Put the editable seed files beside the exe, where a reader will look."""
    (DIST / "configs").mkdir(parents=True, exist_ok=True)
    for src, dst in (
        (ROOT / "configs" / "default.yaml", DIST / "configs" / "default.yaml"),
        (ROOT / "configs" / "events.example.yaml", DIST / "configs" / "events.example.yaml"),
        (ROOT / "current_holdings.example.yaml", DIST / "current_holdings.example.yaml"),
    ):
        if src.exists():
            shutil.copyfile(src, dst)

    (DIST / "READ ME FIRST.txt").write_text(
        f"""{APP}
{'=' * len(APP)}

Double-click "{APP}.exe".

The first run downloads about two years of prices for 49 tickers and takes a
minute or two. After that a cache makes it quick. It needs an internet
connection on that first run.

Windows will probably say "Windows protected your PC" the first time, because
this program is not signed with a paid certificate. Click "More info", then
"Run anyway". Some antivirus tools flag PyInstaller programs for the same
reason. The source is at github.com/HendrySethiawan/idx-quant-screener if you
would rather build it yourself.

YOUR CAPITAL
------------
Open configs\\user.yaml (create it if it is not there) and put in:

    account:
      capital_rp: 25000000     <- your number goes here

Everything in configs\\default.yaml can be changed the same way. Your capital,
your holdings and your trade journal stay in this folder and are never sent
anywhere.

WHAT IS IN THIS FOLDER
----------------------
  {APP}.exe        the program
  configs\\          default.yaml (edit), user.yaml (yours)
  data\\             price cache, the generated terminal, your journal
  logs\\             run logs
  _internal\\        the Python runtime - leave it alone

NOT INVESTMENT ADVICE
---------------------
A personal research tool. Prices come from Yahoo Finance and can be stale or
wrong. Check the live price in your broker before sending any order.
""",
        encoding="utf-8",
    )


def package() -> Path:
    out = ROOT / "dist" / f"{APP.replace(' ', '-')}-windows.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in DIST.rglob("*"):
            if path.is_file():
                z.write(path, Path(APP) / path.relative_to(DIST))
    return out


def _size(path: Path) -> float:
    if path.is_file():
        return path.stat().st_size / 1e6
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the Windows package")
    ap.add_argument("--no-zip", action="store_true", help="leave it as a folder")
    args = ap.parse_args()

    build()
    stage()

    print("\nVerifying the build carries no personal data...")
    problems = verify(DIST)
    if problems:
        print("\nBUILD REJECTED - this must not be distributed:\n")
        for p in problems:
            print(f"  - {p}")
        print("\nFix packaging/idx_terminal.spec, then build again.")
        return 1
    print("  clean: no personal file, directory or capital figure in the build")

    print(f"\n  folder   {_size(DIST):7.1f} MB   {DIST}")
    if not args.no_zip:
        zip_path = package()
        print(f"  zip      {_size(zip_path):7.1f} MB   {zip_path}")

    print(f"\nDone. Unzip anywhere and double-click \"{APP}.exe\".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
