# src/first_run.py
"""
Ask for capital once, before anything is fetched.

The packaged app shipped with no way to set capital: `configs/user.yaml` is not
created by `bootstrap()` -- deliberately, because nothing should invent a capital
figure on someone's behalf -- and the settings editor that was supposed to be the
other route was silently dead. So a first run used the Rp100,000,000 placeholder and
produced a confident ticket to buy Rp30 juta of stock, with nothing anywhere saying
that number was not the reader's money.

This asks. Before the fetch, so nobody waits forty seconds for a ticket sized to a
number they never chose.

**Only when launching the desktop app.** A CLI run or `--browser` warns instead: a
script that stops for input is a script that hangs, and this one is also run from a
scheduler and a build.
"""
from __future__ import annotations

from typing import Optional

# The value shipped in configs/default.yaml. Anything equal to it means "not set".
PLACEHOLDER_CAPITAL = 100_000_000.0


def is_placeholder_capital(settings) -> bool:
    """True while the reader is still on the shipped placeholder."""
    try:
        return abs(float(settings.capital_rp) - PLACEHOLDER_CAPITAL) < 1.0
    except (TypeError, ValueError):
        return False


def has_user_capital(user_config_path: str = "configs/user.yaml") -> bool:
    """
    Whether `configs/user.yaml` already carries a capital.

    Checked separately from the value: somebody whose real capital genuinely is
    Rp100,000,000 has chosen it, and must not be asked again on every launch.
    """
    from pathlib import Path

    import yaml

    path = Path(user_config_path)
    if not path.exists():
        return False
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    return "capital_rp" in (data.get("account") or {})


def should_ask(settings, user_config_path: str = "configs/user.yaml") -> bool:
    return is_placeholder_capital(settings) and not has_user_capital(user_config_path)


def warn_text(settings) -> str:
    """The console warning for every path that cannot ask."""
    return (
        "\n"
        "  !! CAPITAL IS THE PLACEHOLDER !!\n"
        f"  This run is sized for Rp{PLACEHOLDER_CAPITAL:,.0f}, which is almost\n"
        "  certainly not your money. Every lot count below is wrong for your\n"
        "  account until you set it:\n\n"
        "      configs/user.yaml\n"
        "        account:\n"
        "          capital_rp: 10000000\n"
    )


_FORM = """<!doctype html><meta charset="utf-8">
<style>
 :root{color-scheme:dark}
 body{margin:0;background:#0a0e13;color:#d6dce4;
   font:13px/1.5 "Segoe UI",-apple-system,sans-serif;
   display:flex;align-items:center;justify-content:center;height:100vh}
 .box{width:100%;max-width:400px;padding:0 26px}
 h1{font-size:17px;margin:0 0 6px;letter-spacing:-.01em}
 p{color:#67727f;font-size:12px;margin:0 0 16px}
 .in{display:flex;align-items:center;gap:8px;
   background:#161d26;border:1px solid #1e2630;border-radius:7px;padding:9px 12px}
 .in span{color:#67727f;font-size:13px}
 input{flex:1;font:inherit;font-size:19px;font-weight:700;background:transparent;
   border:0;color:#d6dce4;outline:none;font-variant-numeric:tabular-nums}
 .hint{color:#67727f;font-size:11px;margin-top:7px;min-height:15px}
 .row{display:flex;gap:8px;margin-top:18px}
 button{font:inherit;font-size:13px;font-weight:700;cursor:pointer;padding:9px 18px;
   border-radius:6px;border:1px solid #1e2630;background:#161d26;color:#98a3b0}
 button.go{background:#2f7fe0;border-color:transparent;color:#fff;flex:1}
 button:disabled{opacity:.45;cursor:default}
</style>
<div class="box">
  <h1>How much are you investing?</h1>
  <p>This sizes every recommendation. It is saved to configs\\user.yaml on this
     machine and is never sent anywhere.</p>
  <div class="in"><span>Rp</span><input id="v" inputmode="numeric"
       placeholder="10,000,000" autofocus></div>
  <div class="hint" id="h"></div>
  <div class="row">
    <button class="go" id="go" disabled>Start</button>
    <button id="skip">Skip</button>
  </div>
</div>
<script>
 var v=document.getElementById('v'), go=document.getElementById('go'),
     h=document.getElementById('h'), skip=document.getElementById('skip');
 function parse(){ return parseInt((v.value||'').replace(/[^0-9]/g,''),10); }
 function tick(){
   var n=parse();
   if(!n){ h.textContent=''; go.disabled=true; return; }
   v.value=n.toLocaleString('en-US');
   h.textContent = n<500000 ? 'That is below one lot of most IDX names.'
                            : 'Rp'+n.toLocaleString('en-US');
   go.disabled = n<100000;
 }
 v.addEventListener('input',tick);
 function send(val){
   go.disabled=true; skip.disabled=true;
   function done(){ try{ window.pywebview.api.set_capital(val); }catch(e){} }
   if(window.pywebview&&window.pywebview.api){ done(); }
   else { window.addEventListener('pywebviewready',done); }
 }
 go.addEventListener('click',function(){ send(parse()); });
 skip.addEventListener('click',function(){ send(0); });
 v.addEventListener('keydown',function(e){ if(e.key==='Enter'&&!go.disabled) go.click(); });
</script>"""


class _Answer:
    """Receives the number from the form and closes the window."""

    def __init__(self):
        self.value: Optional[float] = None

    def set_capital(self, value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        self.value = number if number > 0 else None
        import webview
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:
                pass
        return {"ok": True}


def ask_capital(logger=None) -> Optional[float]:
    """
    Show the prompt and return what was entered, or None if skipped or unavailable.

    Never raises. This runs before the screener, and a prompt that fails must cost
    nothing more than the prompt -- the run continues on the placeholder, loudly
    flagged.
    """
    try:
        import tempfile
        from pathlib import Path

        import webview
    except Exception:
        return None

    try:
        page = Path(tempfile.mkdtemp()) / "capital.html"
        page.write_text(_FORM, encoding="utf-8")
        answer = _Answer()
        webview.create_window("Set your capital", page.resolve().as_uri(),
                              width=460, height=330, resizable=False, js_api=answer)
        webview.start(gui=None, debug=False, http_server=False)
        return answer.value
    except Exception as e:
        if logger:
            logger.warning(f"Could not show the capital prompt: {e}")
        return None


def apply_capital(value: float, settings=None) -> None:
    """Persist to configs/user.yaml and update the live settings object."""
    from core.config import _apply_overrides, save_user_overrides

    payload = {"account": {"capital_rp": int(value)}}
    save_user_overrides(payload)
    if settings is not None:
        _apply_overrides(settings, payload)
