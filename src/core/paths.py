# src/core/paths.py
"""
Where the app's files live, whether it is running from source or from a .exe.

Every path in this project is relative -- `configs/user.yaml`, `data/output`,
`logs`, `data/journal.csv` -- read from config.py, logger.py, data_fetcher.py,
journal.py, events.py and holdings.py. From source that is fine: you run it from the
repo root. From a double-clicked exe the working directory is whatever Windows
chose, usually not the folder the exe is in, and every one of those paths lands
somewhere unexpected.

`bootstrap()` sets the working directory once, early, and every existing relative
path becomes correct by construction. The alternative -- threading a base directory
through six modules -- is a much larger change with many more places to get wrong,
for the same result.

Config files are copied *out* of the bundle on first run rather than read from
inside it. A config you cannot edit is not a config.

**The reader's files do not live beside the exe.** They used to, and it cost
somebody their capital and their trade log: `packaging/build.py` deletes `dist/`
before every build, and `dist/IDX Terminal/` was exactly where `configs/user.yaml`
and `data/journal.csv` sat. Rebuilding the app erased the app's memory. Unzipping
a new version over an old folder would have done the same. So when frozen those
files live in `%LOCALAPPDATA%\\IDX Terminal\\`, which no build and no unzip can
reach.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import List, Tuple

# Copied out on first run: (bundled source, destination beside the exe).
# `configs/user.yaml` is deliberately NOT here and must never be -- it holds the
# reader's real capital, and shipping it inside a distributable binary would put a
# private number into every copy handed to anyone else.
_SEED_FILES: Tuple[Tuple[str, str], ...] = (
    ("configs/default.yaml", "configs/default.yaml"),
    ("configs/events.example.yaml", "configs/events.example.yaml"),
    ("current_holdings.example.yaml", "current_holdings.example.yaml"),
)

_SEED_DIRS: Tuple[str, ...] = ("configs", "data", "data/output", "logs")


def is_frozen() -> bool:
    """True when running from a PyInstaller build rather than the source tree."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """
    The folder the reader's own files belong in.

    Frozen: next to the executable, so configs and data sit where they can be found
    and edited. From source: the repo root, which is what every relative path in the
    project already assumes.
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


APP_FOLDER_NAME = "IDX Terminal"

# Copied across from an older install that kept its files beside the exe.
_MIGRATE: Tuple[str, ...] = ("configs", "data", "logs", "current_holdings.yaml")


def data_dir() -> Path:
    """
    Where the reader's own files live: capital, journal, cash ledger, cache.

    Frozen: a per-user folder outside any install directory. This is the whole
    point -- see the module docstring. Falls back to the home directory on a
    machine with no LOCALAPPDATA, because failing to start is not an option.

    From source: the repo root, unchanged. Every relative path in the project
    already assumes it, and moving a developer's working files would be a
    surprise with no upside.
    """
    if not is_frozen():
        return app_dir()

    base = os.environ.get("LOCALAPPDATA") or ""
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / APP_FOLDER_NAME


def migrate_from(source: Path, target: Path) -> List[Path]:
    """
    Move an older install's files into the per-user folder, once.

    Only ever copies into a gap: anything already in `target` wins, so running
    this twice is a no-op and a newer file is never clobbered by an older one.
    Returns what was copied, for the log.
    """
    moved: List[Path] = []
    if source.resolve() == target.resolve():
        return moved

    for rel in _MIGRATE:
        src = source / rel
        if not src.exists():
            continue
        dst = target / rel
        try:
            if src.is_dir():
                for item in src.rglob("*"):
                    if item.is_dir():
                        continue
                    out = dst / item.relative_to(src)
                    if out.exists():
                        continue
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(item, out)
                    moved.append(out)
            elif not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                moved.append(dst)
        except OSError:
            # A file that cannot be copied is not a reason to refuse to start.
            continue
    return moved


def bundled(relative: str) -> Path:
    """
    A read-only file that shipped inside the build.

    PyInstaller unpacks those to `sys._MEIPASS`; from source they are just in the
    repo. Either way this is where a seed file is read *from*, never written to.
    """
    root = Path(getattr(sys, "_MEIPASS", "")) if is_frozen() else app_dir()
    return (root or app_dir()) / relative


def bootstrap(chdir: bool = True) -> List[Path]:
    """
    Make the app's folder usable, and return the files that were created.

    Idempotent, and it never overwrites something that already exists. That matters
    most for `configs/user.yaml`: it holds the reader's capital, and a launcher that
    reset it every run would be worse than no launcher at all. Nothing here touches
    that file -- it is created by `save_user_overrides` or by hand.
    """
    base = data_dir()
    if chdir and is_frozen():
        base.mkdir(parents=True, exist_ok=True)
        # Before the chdir, so an older install's files are found by their old
        # relative paths and end up where the new ones will be looked for.
        migrate_from(app_dir(), base)
        os.chdir(base)

    created: List[Path] = []
    for rel in _SEED_DIRS:
        target = base / rel
        if not target.exists():
            target.mkdir(parents=True, exist_ok=True)
            created.append(target)

    for src_rel, dst_rel in _SEED_FILES:
        target = base / dst_rel
        if target.exists():
            continue
        source = bundled(src_rel)
        if not source.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(source, target)
            created.append(target)
        except OSError:
            # A read-only install location is not a reason to refuse to start; the
            # defaults are already compiled in and the run can proceed without a
            # copy on disk.
            pass

    return created
