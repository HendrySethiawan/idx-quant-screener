# src/core/version.py
"""
Which build produced this page.

The whole discipline of this project is knowing which configuration produced which
number. `evidence_note` refuses to quote a verdict whose config fingerprint does not
match; the backtest records what it ran against; the README is about to stop
asserting figures it cannot regenerate. And yet a `brief.html` saved to disk and
re-read in six months carried nothing at all saying what made it -- so a number that
looked wrong could not be traced to the code that wrote it, which is the one question
worth asking about an old report.

`1.0.0` rather than `0.x`. It has run in daily production for months and real money
moves on its output; a leading zero would be false modesty.

**The stamp never raises and never blocks.** It is decoration on a report. Three
sources, in order:

    frozen build   `_build.txt`, written into the bundle by packaging/build.py
    from source    `git rev-parse --short HEAD`
    neither        "dev"

The frozen case needs a file because a PyInstaller bundle has no `.git` and no git
binary, so the commit has to be baked in at build time or it is gone.
"""
from __future__ import annotations

import subprocess
from functools import lru_cache

__version__ = "1.0.0"

# The stamp file inside a frozen build. Written by packaging/build.py, read by
# `commit()`. Deliberately not in `_SEED_FILES`: it belongs to the build, is never
# edited, and must not be copied out into the reader's folder where an upgrade would
# leave a stale one behind.
BUILD_FILE = "_build.txt"


def _from_bundle() -> str:
    """The commit baked in at build time, or "" if this is not a frozen build."""
    try:
        from core.paths import bundled, is_frozen

        if not is_frozen():
            return ""
        path = bundled(BUILD_FILE)
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""
    except Exception:
        # A missing stamp is a cosmetic loss. It must not take a run with it.
        return ""


def _from_git() -> str:
    """The working tree's commit, or "" when git is absent or this is not a repo."""
    try:
        from core.paths import app_dir

        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(app_dir()),
            capture_output=True,
            text=True,
            timeout=5,
            # Windows: keep a console window from flashing up behind the app.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        # git missing, not a repository, or slower than the timeout.
        return ""


@lru_cache(maxsize=1)
def commit() -> str:
    """
    The short commit this is running from, or "dev".

    Cached: `build_stamp()` is called once per report today, but a subprocess per
    call is the kind of cost that only shows up once something renders in a loop.
    """
    return _from_bundle() or _from_git() or "dev"


@lru_cache(maxsize=1)
def build_stamp() -> str:
    """
    The one-line provenance string the reports print, e.g. `v1.0.0 (66c815f)`.

    Safe to interpolate into HTML: both halves are ASCII by construction -- a
    semantic version and a git short hash -- so there is nothing here to escape.
    """
    return f"v{__version__} ({commit()})"
