# src/desktop.py
"""
Open the brief in a native window instead of a browser tab.

pywebview wraps the OS webview -- on Windows that is the Edge WebView2 runtime, so
the page renders in the same engine it would in a browser, without the address bar
and tab strip sitting above a terminal layout.

Two rules this module exists to keep:

  * **A new dependency must never become a way for the tool to stop working.**
    Every failure path -- pywebview absent, no WebView2 runtime, a headless or
    remote session, an exception from the backend -- returns False so the caller
    falls back to `webbrowser.open`, which is what the tool did before this existed.

  * **Closing the window must end the process.** `webview.start()` returns when the
    last window closes, and anything that left a non-daemon thread behind would keep
    the interpreter alive and leave a stray python.exe in Task Manager after every
    run. So this takes no callbacks, starts no threads, and runs no HTTP server;
    `start()` is called on the main thread and nothing outlives it.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Optional, Tuple

DEFAULT_SIZE = (1600, 950)
MIN_SIZE = (1000, 640)


def available() -> bool:
    """True if a native window could be opened. Cheap: an import, no window."""
    try:
        import webview  # noqa: F401
    except Exception:
        return False
    return True


def _stray_threads(before: List[threading.Thread]) -> List[str]:
    """
    Non-daemon threads that appeared and are still running.

    A daemon thread dies with the interpreter; a non-daemon one keeps it alive, and
    that is the difference between the window closing and the process closing.
    """
    known = {id(t) for t in before}
    return [
        t.name for t in threading.enumerate()
        if id(t) not in known and t.is_alive() and not t.daemon
    ]


def launch(
    path,
    title: str = "IDX Terminal",
    size: Tuple[int, int] = DEFAULT_SIZE,
    logger=None,
    js_api=None,
) -> bool:
    """
    Show `path` in a native window and block until it is closed.

    Returns True if the window ran, False if the caller should fall back to a
    browser. Never raises: a layout tool failing to open is a nuisance, but it must
    not take the run with it after the analysis already succeeded.
    """
    target = Path(path)
    if not target.exists():
        return False

    try:
        import webview
    except Exception as e:                       # not installed, or a broken install
        if logger:
            logger.info(f"Native window unavailable ({e}); using the browser instead")
        return False

    before = threading.enumerate()
    try:
        webview.create_window(
            title,
            target.resolve().as_uri(),
            width=int(size[0]),
            height=int(size[1]),
            min_size=MIN_SIZE,
            resizable=True,
            text_select=True,                    # it is a report; let people copy from it
            # Exposed to the page as window.pywebview.api. This is what lets the
            # terminal record a trade without a web server. Optional: a page opened
            # as a plain file simply finds no bridge and shows the CLI command.
            js_api=js_api,
        )
        # No `func`, so nothing runs alongside the window and there is no worker to
        # outlive it. No http_server: the page is self-contained and file:// is
        # enough, which also keeps it from opening a listening socket.
        webview.start(gui=None, debug=False, http_server=False)
    except Exception as e:
        if logger:
            logger.warning(f"Native window failed ({e}); using the browser instead")
        return False

    stray = _stray_threads(before)
    if stray and logger:
        # Worth saying out loud rather than leaving a process to be discovered later.
        logger.warning(
            f"Window closed but these non-daemon threads are still running: "
            f"{', '.join(stray)}. The process may not exit on its own."
        )
    return True


def open_result(path, prefer_desktop: bool = True, title: str = "IDX Terminal",
                logger=None, js_api=None) -> str:
    """
    Show the finished brief. Returns which route was taken, for the caller to print.

    The browser fallback is also wrapped: on a machine with no default browser
    registered, `webbrowser.open` raises, and the run has already done its job by
    then -- the file is on disk and the console summary is printed.
    """
    if prefer_desktop and launch(path, title=title, logger=logger, js_api=js_api):
        return "desktop"

    import webbrowser
    try:
        webbrowser.open(Path(path).resolve().as_uri())
        return "browser"
    except Exception:
        return "none"
