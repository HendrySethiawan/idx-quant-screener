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
