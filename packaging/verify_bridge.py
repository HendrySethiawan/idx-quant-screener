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
  ready(function(){
    var f = document.getElementById('trade-form');
    if(!f){ window.pywebview.api.report({stage:'no-form',
      note:'the page rendered the CLI fallback, so the app thought there was no bridge'});
      return; }
    document.querySelector("input[name=tf-action][value=BUY]").checked = true;
    document.getElementById('tf-ticker').value = 'BBRI';
    document.getElementById('tf-lots').value   = '3';
    document.getElementById('tf-price').value  = '4150';
    document.getElementById('tf-date').value   = '2026-08-23';
    // Fire the same event a keystroke fires, so the page's own preview handler runs.
    ['tf-ticker','tf-lots','tf-price'].forEach(function(id){
      document.getElementById(id).dispatchEvent(new Event('input',{bubbles:true}));
    });
    setTimeout(function(){
      var preview = document.getElementById('tf-preview').textContent || '';
      document.getElementById('tf-submit').click();
      setTimeout(function(){
        window.pywebview.api.report({
          stage: 'done',
          preview_filled: preview.indexOf('Gross') !== -1,
          message: (document.getElementById('tf-msg')||{}).textContent || '',
          ledger_has_bbri: (document.getElementById('ledger')||{}).innerHTML
                             .indexOf('BBRI') !== -1
        });
      }, 1500);
    }, 1200);
  });
})();
"""


def main() -> int:
    if not BRIEF.exists():
        print(f"No brief at {BRIEF}. Run the app once first.")
        return 2

    # A scratch journal: this records a real trade, and it must not be yours.
    tmp = Path(tempfile.mkdtemp())
    settings = load_settings("configs/default.yaml")
    settings.account = {**settings.account,
                        "journal_path": str(tmp / "journal.csv"),
                        "marks_path": str(tmp / "marks.csv"),
                        "holdings_path": str(tmp / "holdings.yaml")}

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
            if RESULT.get("stage") in ("done", "no-form", "driver-failed"):
                break
        try:
            window.destroy()
        except Exception:
            pass

    threading.Thread(target=drive, daemon=True).start()
    webview.start(gui=None, debug=False, http_server=False)

    journal = tmp / "journal.csv"
    rows = pd.read_csv(journal) if journal.exists() else pd.DataFrame()

    print("\n--- what the real page did ---")
    print(json.dumps(RESULT, indent=2))
    print(f"\njournal rows written: {len(rows)}")
    if not rows.empty:
        print(rows[["date", "ticker", "action", "lots", "price",
                    "fee_rp", "stamp_rp", "net_rp"]].to_string(index=False))

    ok = (RESULT.get("stage") == "done"
          and RESULT.get("preview_filled") is True
          and len(rows) == 1
          and rows.iloc[0]["ticker"] == "BBRI.JK")

    print("\n" + ("PASS - the page reached Python and the trade was recorded."
                  if ok else "FAIL - the bridge did not work end to end."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
