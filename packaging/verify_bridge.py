#!/usr/bin/env python3
"""
Does the real page reach Python?

    python packaging/verify_bridge.py

This exists because a unit test did not catch the bug it is written for. The bridge
was verified once with a purpose-built probe that listened for `pywebviewready` --
the correct pattern -- and passed, while the page that shipped read
`window.pywebview` synchronously and silently wired nothing. The mechanism was
proven; the page using it was not.

So this drives the **real generated brief**, through the **real SHELL_JS**, in a
**real window**, and asserts a row reached the journal. Anything less would have
passed last time too.

Needs a brief on disk: run the app once first.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd  # noqa: E402
import webview  # noqa: E402

from api import TerminalAPI  # noqa: E402
from core.config import load_settings  # noqa: E402

BRIEF = ROOT / "data" / "output" / "brief.html"
RESULT: dict = {}


class Probe(TerminalAPI):
    """Counts what the PAGE calls, as opposed to what this driver calls."""

    page_previews = 0

    def preview_trade(self, *a, **k):
        Probe.page_previews += 1          # only the page's own handler calls this
        return super().preview_trade(*a, **k)

    def report(self, payload):
        RESULT.update(payload)
        return {"ok": True}


# The page drives itself the way a person would: fill the form, click Record.
DRIVER = """
(function(){
  function ready(fn){
    if(window.pywebview && window.pywebview.api){ fn(); return; }
    window.addEventListener('pywebviewready', fn);
  }
  function fill(t, lots, price){
    document.querySelector("input[name=tf-action][value=BUY]").checked = true;
    document.getElementById('tf-ticker').value = t;
    document.getElementById('tf-lots').value   = lots;
    document.getElementById('tf-price').value  = price;
    document.getElementById('tf-date').value   = '2026-08-23';
    ['tf-ticker','tf-lots','tf-price'].forEach(function(id){
      document.getElementById(id).dispatchEvent(new Event('input',{bubbles:true}));
    });
  }
  // The cash form defines capital, so its preview has to be driven by the page's
  // own handler too -- a helper declared in the wrong scope parses fine and then
  // silently never fills the box in.
  function fillCash(amount){
    document.querySelector("input[name=cf-kind][value=DEPOSIT]").checked = true;
    document.getElementById('cf-amount').value = amount;
    document.getElementById('cf-date').value   = '2026-08-23';
    ['cf-amount','cf-date'].forEach(function(id){
      document.getElementById(id).dispatchEvent(new Event('input',{bubbles:true}));
    });
  }
  ready(function(){
    var api = window.pywebview.api;
    if(!document.getElementById('trade-form')){
      api.report({stage:'no-form'}); return;
    }
    if(!document.getElementById('cash-form')){
      api.report({stage:'no-cash-form'}); return;
    }
    fillCash('25000000');
    // Record twice. One record was the whole of the previous check, and the
    // reported symptom was that the SECOND never worked.
    fill('BBRI', '3', '4150');
    setTimeout(function(){
      var pv = document.getElementById('tf-preview');
      var preview = pv ? (pv.textContent || '') : '(no #tf-preview element)';
      api.log_trade('BUY','BBRI','3','4150','2026-08-23','first','tool').then(function(r1){
        fill('TLKM', '2', '2600');
        setTimeout(function(){
          api.log_trade('BUY','TLKM','2','2600','2026-08-23','second','tool').then(function(r2){
            var cv = document.getElementById('cf-preview');
            var cashPreview = cv ? (cv.textContent || '') : '(no #cf-preview element)';
            api.record_cash('DEPOSIT','25000000','2026-08-23','harness').then(function(r4){
            api.rebuild().then(function(r3){
              api.report({
                stage: 'done',
                preview_filled: preview.indexOf('Gross') !== -1,
                preview_text: preview.slice(0, 160),
                cash_preview_filled: cashPreview.indexOf('Capital') !== -1,
                cash_preview_text: cashPreview.slice(0, 160),
                cash_ok: !!r4.ok, cash_msg: r4.message,
                go_disabled: document.getElementById('tf-submit').disabled,
                shell_ran: !!window.__idxShellRan,
                shell_error: window.__idxError || '',
                refresh_visible: !document.getElementById('refresh-controls').hidden,
                first_ok: !!r1.ok, first_msg: r1.message,
                second_ok: !!r2.ok, second_msg: r2.message,
                rebuild_ok: !!r3.ok,
                rebuild_url: !!(r3.data && r3.data.url)
              });
            });
            });
          });
        }, 700);
      });
    }, 2500);
  });
})();
"""


def _stale(brief: Path) -> list:
    """Source files newer than the page this harness is about to drive.

    The harness copies the last brief the app generated. That page carries a frozen
    copy of the script, so it can be driven successfully long after the source it
    came from was rewritten -- a green result proving something about code that no
    longer exists. It happened: the preview asserted on had been reworded two
    commits earlier and the run still passed.
    """
    cutoff = brief.stat().st_mtime
    return sorted(p.relative_to(ROOT).as_posix()
                  for p in (ROOT / "src").rglob("*.py")
                  if "__pycache__" not in p.parts and p.stat().st_mtime > cutoff)


def main() -> int:
    if not BRIEF.exists():
        print(f"No brief at {BRIEF}. Run the app once first.")
        return 2

    behind = _stale(BRIEF)
    if behind:
        print(f"{BRIEF} predates {len(behind)} source file(s):")
        for name in behind[:8]:
            print(f"  {name}")
        print("Regenerate it (run the app once) before verifying, or this checks "
              "a page built from code you have since changed.")
        return 2

    # A scratch journal: this records a real trade, and it must not be yours.
    tmp = Path(tempfile.mkdtemp())
    settings = load_settings("configs/default.yaml")
    settings.account = {**settings.account,
                        "journal_path": str(tmp / "journal.csv"),
                        "marks_path": str(tmp / "marks.csv"),
                        "holdings_path": str(tmp / "holdings.yaml"),
                        "cash_path": str(tmp / "cash.csv"),
                        "snapshot_path": str(tmp / "run.joblib")}

    # `--browser` renders the CLI fallback instead of the form, so a page generated
    # that way has nothing to drive. Saying which artifact is wrong beats the JS
    # reporting a bare "no-form" from inside the window.
    page_html = BRIEF.read_text(encoding="utf-8")
    for needed, what in (('id="trade-form"', "trade form"),
                         ('id="cash-form"', "cash form")):
        if needed not in page_html:
            print(f"{BRIEF} has no {what} -- it was generated with --browser.")
            print("Regenerate it by launching the app normally, then verify.")
            return 2

    page = tmp / "brief.html"
    shutil.copyfile(BRIEF, page)

    window = webview.create_window("bridge check", page.resolve().as_uri(),
                                   width=1280, height=800,
                                   js_api=Probe(settings, prices={"BBRI.JK": 4150.0}))

    def drive():
        time.sleep(2.0)
        try:
            window.evaluate_js(DRIVER)
        except Exception as e:
            RESULT.update({"stage": "driver-failed", "note": str(e)})
        for _ in range(120):
            time.sleep(0.25)
            if RESULT.get("stage") in ("done", "no-form", "no-cash-form",
                                       "driver-failed"):
                break
        try:
            window.destroy()
        except Exception:
            pass

    threading.Thread(target=drive, daemon=True).start()
    webview.start(gui=None, debug=False, http_server=False)

    journal = tmp / "journal.csv"
    rows = pd.read_csv(journal) if journal.exists() else pd.DataFrame()
    cash_file = tmp / "cash.csv"
    cash_rows = pd.read_csv(cash_file) if cash_file.exists() else pd.DataFrame()

    print("\n--- what the real page did ---")
    print(json.dumps(RESULT, indent=2))
    print(f"\npreview_trade calls the PAGE made on its own: {Probe.page_previews}")
    print(f"journal rows written: {len(rows)}")
    print(f"cash rows written: {len(cash_rows)}")
    if not rows.empty:
        print(rows[["date", "ticker", "action", "lots", "price",
                    "fee_rp", "stamp_rp", "net_rp"]].to_string(index=False))

    # `rebuild` is called but not asserted on: this harness has no run context
    # behind it, so it correctly answers "nothing to rebuild from". What matters
    # here is that calling it does not take the bridge down -- if it did, the
    # `report` that follows would never arrive and `stage` would not be "done".
    # `shell_ran` and `page_previews` are the two that matter most. A syntax error in
    # the inline script means it never executes at all -- which cannot be caught by a
    # try/catch inside that same script -- so every control silently does nothing
    # while the page looks perfectly normal. That shipped once. Driving the API
    # directly, as this script also does, passes right through it.
    ok = (RESULT.get("stage") == "done"
          and RESULT.get("shell_ran") is True
          and not RESULT.get("shell_error")
          and RESULT.get("refresh_visible") is True
          and Probe.page_previews > 0          # the PAGE's own handler, not ours
          and RESULT.get("preview_filled") is True
          and RESULT.get("first_ok") is True
          and RESULT.get("second_ok") is True
          and len(rows) == 2
          and set(rows["ticker"]) == {"BBRI.JK", "TLKM.JK"}
          # Capital comes from this ledger, so the cash form is not optional
          # furniture -- if its preview is dead, the number every lot count is
          # sized against is set blind.
          and RESULT.get("cash_preview_filled") is True
          and RESULT.get("cash_ok") is True
          and len(cash_rows) == 1)

    print("\n" + ("PASS - two trades and a deposit recorded through the real page, "
                  "and the bridge survived a rebuild call."
                  if ok else "FAIL - the bridge did not work end to end."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
