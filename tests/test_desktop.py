"""
Tests for the native-window launcher.

Two properties, both of which fail quietly if they break:

  * **A new dependency must not become a way for the tool to stop working.** Every
    failure path has to return False so the caller falls back to a browser. The
    analysis has already succeeded and been written to disk by the time this runs;
    a window failing to open must not lose it.

  * **Closing the window must end the process.** `webview.start()` returns when the
    last window closes, but any non-daemon thread left behind keeps the interpreter
    alive and leaves a stray python.exe in Task Manager after every single run.
    Nobody notices that for weeks.
"""
import sys
import threading
import types

import pytest

from desktop import DEFAULT_SIZE, available, launch, open_result


@pytest.fixture
def page(tmp_path):
    f = tmp_path / "brief.html"
    f.write_text("<!doctype html><body>hi</body>", encoding="utf-8")
    return f


class _FakeWebview(types.ModuleType):
    """A webview that opens instantly and closes instantly."""

    def __init__(self, on_start=None, fail_create=False):
        super().__init__("webview")
        self.windows = []
        self.started = []
        self._on_start = on_start
        self._fail_create = fail_create

    def create_window(self, title, url=None, **kw):
        if self._fail_create:
            raise RuntimeError("no webview runtime")
        self.windows.append({"title": title, "url": url, **kw})
        return object()

    def start(self, *a, **kw):
        self.started.append(kw)
        if self._on_start:
            self._on_start()


def _install(monkeypatch, module):
    monkeypatch.setitem(sys.modules, "webview", module)
    return module


# ---------------------------------------------------- it must never take the run
def test_a_missing_pywebview_is_not_an_error(monkeypatch, page):
    """The whole point of the fallback: no pywebview, no problem."""
    monkeypatch.setitem(sys.modules, "webview", None)   # import returns None -> fails
    assert launch(page) is False


def test_a_broken_backend_falls_back_rather_than_raising(monkeypatch, page):
    _install(monkeypatch, _FakeWebview(fail_create=True))
    assert launch(page) is False


def test_a_failure_inside_start_falls_back(monkeypatch, page):
    def boom():
        raise RuntimeError("display not found")
    _install(monkeypatch, _FakeWebview(on_start=boom))
    assert launch(page) is False


def test_a_missing_file_is_refused_before_any_window_opens(monkeypatch, tmp_path):
    fake = _install(monkeypatch, _FakeWebview())
    assert launch(tmp_path / "nope.html") is False
    assert fake.windows == []


def test_open_result_falls_back_to_the_browser(monkeypatch, page):
    _install(monkeypatch, _FakeWebview(fail_create=True))
    opened = {}
    monkeypatch.setattr("webbrowser.open", lambda u: opened.setdefault("url", u))
    assert open_result(page) == "browser"
    assert opened["url"].startswith("file:")


def test_open_result_survives_having_no_browser_either(monkeypatch, page):
    """Everything has already been written by now. Report it and move on."""
    _install(monkeypatch, _FakeWebview(fail_create=True))

    def boom(_u):
        raise RuntimeError("no browser registered")
    monkeypatch.setattr("webbrowser.open", boom)
    assert open_result(page) == "none"


def test_browser_can_be_forced_without_touching_pywebview(monkeypatch, page):
    fake = _install(monkeypatch, _FakeWebview())
    monkeypatch.setattr("webbrowser.open", lambda u: None)
    assert open_result(page, prefer_desktop=False) == "browser"
    assert fake.windows == [], "pywebview was started despite --browser"


# --------------------------------------------------- QA: the process must exit
def test_closing_the_window_leaves_no_thread_holding_the_process_open(monkeypatch, page):
    """
    The zombie-process check. A non-daemon thread outliving `start()` keeps
    python.exe running after the window is gone, once per run, forever.
    """
    _install(monkeypatch, _FakeWebview())
    before = {id(t) for t in threading.enumerate()}

    assert launch(page) is True

    leftover = [t.name for t in threading.enumerate()
                if id(t) not in before and t.is_alive() and not t.daemon]
    assert not leftover, f"these would keep the process alive: {leftover}"


def test_launch_starts_nothing_alongside_the_window(monkeypatch, page):
    """
    No `func` and no http_server. A worker thread or a listening socket is exactly
    what would outlive the window, so the safest version is the one that has none.
    """
    fake = _install(monkeypatch, _FakeWebview())
    assert launch(page) is True

    assert len(fake.started) == 1
    kwargs = fake.started[0]
    assert kwargs.get("http_server") is False, "an http server would outlive the window"
    assert fake.started[0].get("debug") is False


def test_launch_registers_no_callbacks(monkeypatch, page):
    """A callback is a path by which something can still be running after the close."""
    fake = _install(monkeypatch, _FakeWebview())
    launch(page)
    window = fake.windows[0]
    assert not any(k.startswith("on_") for k in window), window


# ------------------------------------------------------------------- the window
def test_the_window_gets_the_file_and_a_usable_size(monkeypatch, page):
    fake = _install(monkeypatch, _FakeWebview())
    assert launch(page, title="IDX Terminal") is True

    window = fake.windows[0]
    assert window["title"] == "IDX Terminal"
    assert window["url"].startswith("file:") and window["url"].endswith("brief.html")
    assert (window["width"], window["height"]) == DEFAULT_SIZE
    assert window["resizable"] is True
    # A report you cannot copy a ticker out of is worse than a browser tab.
    assert window["text_select"] is True


def test_the_window_has_a_floor_below_the_layouts_breakpoints(monkeypatch, page):
    """min_size must not let the window shrink into the squashed band."""
    fake = _install(monkeypatch, _FakeWebview())
    launch(page)
    assert fake.windows[0]["min_size"][0] >= 900


def test_available_reports_whether_a_window_is_possible(monkeypatch):
    _install(monkeypatch, _FakeWebview())
    assert available() is True
    monkeypatch.setitem(sys.modules, "webview", None)
    assert available() is False
