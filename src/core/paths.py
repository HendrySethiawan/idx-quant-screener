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

Config files are copied *out* beside the exe on first run rather than read from
inside the bundle. A config you cannot edit is not a config.
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
    base = app_dir()
    if chdir and is_frozen():
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
