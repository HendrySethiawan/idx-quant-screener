# src/first_run.py
"""
Whether capital is still the shipped placeholder, and how to say so.

The packaged app shipped with no way to set capital, so a first run used the
Rp100,000,000 placeholder and produced a confident ticket to buy Rp30 juta of stock
with nothing anywhere saying that number was not the reader's money.

The fix was a prompt window before the fetch. **That is gone.** It opened a second
pywebview window in the same process that goes on to open the main one, it was one
click to dismiss, and dismissing it put you straight back where you started -- which
is what "why do I have to set my capital every time" was describing. Recording a
deposit on the Portfolio page sets capital now, and the banner on every page points
at it. What remains here is the detection and the wording.
"""
from __future__ import annotations

from typing import Optional

# The value shipped in configs/default.yaml. Anything equal to it means "not set".
PLACEHOLDER_CAPITAL = 100_000_000.0


def capital_equals_placeholder(settings) -> bool:
    """
    Whether the VALUE happens to equal the shipped figure. Not a verdict.

    Deliberately not called `is_placeholder_capital` any more. Under that name it
    read like the complete test and was used as one: the banner on every page ran
    off it, so a reader whose capital genuinely is Rp100,000,000 -- a perfectly
    ordinary round number, and the one the app ships with -- was told on every
    launch that the figures were sized for money that was not theirs, with no way
    to dismiss it. `should_ask` is the verdict. This is one of its two halves.
    """
    try:
        return abs(float(settings.capital_rp) - PLACEHOLDER_CAPITAL) < 1.0
    except (TypeError, ValueError):
        return False


def has_user_capital(user_config_path: Optional[str] = None) -> bool:
    """
    Whether `configs/user.yaml` already carries a capital.

    Checked separately from the value: somebody whose real capital genuinely is
    Rp100,000,000 has chosen it, and must not be asked again on every launch.

    The path is resolved from `core.config.USER_CONFIG_PATH` at call time rather
    than bound as a literal default. The autouse test fixture redirects that
    constant to a tmp file so no test can read or write the reader's real config,
    and a literal here would walk straight past the redirect -- making the result
    depend on whatever happens to be in the developer's own account settings.
    """
    from pathlib import Path

    import yaml

    if user_config_path is None:
        from core.config import USER_CONFIG_PATH
        user_config_path = USER_CONFIG_PATH

    path = Path(user_config_path)
    if not path.exists():
        return False
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    return "capital_rp" in (data.get("account") or {})


def has_recorded_cash(settings) -> bool:
    """
    Whether the cash ledger has anything in it.

    Recording a deposit *is* setting your capital -- `portfolio.cash.sync_capital`
    derives the figure from the ledger and `save_setting` refuses the config route
    once a row exists. But that path writes no `capital_rp` to `user.yaml`, so
    somebody whose deposits total exactly Rp100,000,000 would be told their
    capital was unset for having done the very thing the banner asks for.
    """
    try:
        from portfolio.cash import cash_path, load_cash
        return not load_cash(cash_path(settings)).empty
    except Exception:
        # A missing or unreadable ledger is not evidence of a choice, and it must
        # never be the reason a run fails to start.
        return False


def should_ask(settings, user_config_path: Optional[str] = None) -> bool:
    """
    The verdict: is capital still unset?

    Both halves matter. The value alone cannot tell the shipped default from a
    reader who chose that same number, and the config file alone misses anyone
    whose capital comes from the cash ledger instead.
    """
    if not capital_equals_placeholder(settings):
        return False
    return not (has_user_capital(user_config_path) or has_recorded_cash(settings))


def warn_text(settings) -> str:
    """
    The console warning for every path that has no window to say it in.

    Points at the cash ledger first, because that is where capital comes from now
    -- recording what you paid in *is* setting it. The config route still works for
    anyone who has not recorded a deposit, so it is named second rather than
    dropped. The example figure is deliberately not a real one: this repository is
    public, and an example is the easiest place for somebody's actual balance to
    end up in a commit.
    """
    return (
        "\n"
        "  !! CAPITAL IS THE PLACEHOLDER !!\n"
        f"  This run is sized for Rp{PLACEHOLDER_CAPITAL:,.0f}, which is almost\n"
        "  certainly not your money. Every lot count below is wrong for your\n"
        "  account until you set it.\n\n"
        "  In the app: Portfolio -> Cash in and out -> record what you paid in.\n\n"
        "  Or by hand, in configs/user.yaml:\n"
        "        account:\n"
        "          capital_rp: 25000000\n"
    )


def apply_capital(value: float, settings=None) -> None:
    """
    Persist to configs/user.yaml and update the live settings object.

    Still here for anyone who has not recorded a deposit: the Settings field writes
    through this. Once the cash ledger has a row it drives capital instead, and
    `TerminalAPI.save_setting` refuses this route rather than letting two numbers
    disagree.
    """
    from core.config import _apply_overrides, save_user_overrides

    payload = {"account": {"capital_rp": int(value)}}
    save_user_overrides(payload)
    if settings is not None:
        _apply_overrides(settings, payload)
