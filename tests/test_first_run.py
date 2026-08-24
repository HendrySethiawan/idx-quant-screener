"""
Tests for the capital prompt and the placeholder guard.

These exist because of a real failure: the packaged app ran on the shipped
Rp100,000,000 placeholder and produced a confident ticket to buy Rp30 juta of stock
in a Rp10 juta account, with nothing anywhere saying the figure was not the reader's
money. Both halves of that -- no way to set it, no warning that it was unset -- are
what these guard.
"""
import builtins
from pathlib import Path

import pytest
import yaml

from first_run import (PLACEHOLDER_CAPITAL, apply_capital, has_user_capital,
                       is_placeholder_capital, should_ask, warn_text)


# ------------------------------------------------------------ detecting it
def test_the_shipped_default_is_recognised_as_unset(settings_mock):
    settings_mock.account = {**settings_mock.account, "capital_rp": PLACEHOLDER_CAPITAL}
    assert is_placeholder_capital(settings_mock) is True


def test_a_real_figure_is_not_the_placeholder(settings_mock):
    settings_mock.account = {**settings_mock.account, "capital_rp": 10_000_000}
    assert is_placeholder_capital(settings_mock) is False


def test_a_broken_capital_is_not_mistaken_for_the_placeholder(settings_mock):
    settings_mock.account = {**settings_mock.account, "capital_rp": "lots"}
    assert is_placeholder_capital(settings_mock) is False


def test_someone_whose_capital_really_is_the_placeholder_is_not_asked_twice(
        settings_mock, tmp_path):
    """
    A person with exactly Rp100,000,000 has chosen it. The file, not the value, is
    what says whether a choice was made.
    """
    settings_mock.account = {**settings_mock.account, "capital_rp": PLACEHOLDER_CAPITAL}
    user = tmp_path / "user.yaml"
    user.write_text("account:\n  capital_rp: 100000000\n", encoding="utf-8")

    assert has_user_capital(str(user)) is True
    assert should_ask(settings_mock, str(user)) is False


def test_asking_happens_when_nothing_has_been_chosen(settings_mock, tmp_path):
    settings_mock.account = {**settings_mock.account, "capital_rp": PLACEHOLDER_CAPITAL}
    assert should_ask(settings_mock, str(tmp_path / "absent.yaml")) is True


def test_a_user_file_without_a_capital_still_counts_as_unset(settings_mock, tmp_path):
    settings_mock.account = {**settings_mock.account, "capital_rp": PLACEHOLDER_CAPITAL}
    user = tmp_path / "user.yaml"
    user.write_text("account:\n  min_positions: 4\n", encoding="utf-8")
    assert has_user_capital(str(user)) is False
    assert should_ask(settings_mock, str(user)) is True


def test_an_unreadable_user_file_is_treated_as_unset(settings_mock, tmp_path):
    user = tmp_path / "user.yaml"
    user.write_text("account: [this is not\n  valid: yaml", encoding="utf-8")
    assert has_user_capital(str(user)) is False


# --------------------------------------------------------------- saving it
def test_the_answer_is_written_and_applied(settings_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    apply_capital(10_000_000, settings_mock)

    saved = yaml.safe_load((tmp_path / "configs" / "user.yaml").read_text(encoding="utf-8"))
    assert saved["account"]["capital_rp"] == 10_000_000
    assert settings_mock.capital_rp == 10_000_000


def test_saving_capital_keeps_other_overrides(settings_mock, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "user.yaml").write_text(
        "account:\n  min_positions: 4\n", encoding="utf-8")

    apply_capital(12_500_000, settings_mock)
    saved = yaml.safe_load((tmp_path / "configs" / "user.yaml").read_text(encoding="utf-8"))
    assert saved["account"]["capital_rp"] == 12_500_000
    assert saved["account"]["min_positions"] == 4


# ------------------------------------------------- it must never block a script
def test_nothing_here_reads_stdin(monkeypatch, settings_mock):
    """
    A prompt that blocks is a scheduled run that hangs. The window is the only way
    this asks anything.
    """
    def boom(*a, **k):
        raise AssertionError("input() would hang a non-interactive run")

    monkeypatch.setattr(builtins, "input", boom)
    should_ask(settings_mock)
    is_placeholder_capital(settings_mock)
    warn_text(settings_mock)


def test_there_is_no_prompt_window_left_to_open():
    """
    The pre-flight capital window is gone and must not come back.

    It opened a second pywebview window in the same process that goes on to open
    the main one, and it was one click to dismiss -- after which the run was sized
    for Rp100,000,000 of somebody else's money. Recording a deposit sets capital
    now. A test, because deleting it is only half the decision.
    """
    import first_run

    assert not hasattr(first_run, "ask_capital")
    # The docstring explains why it went, so look for the code rather than the word.
    source = Path(first_run.__file__).read_text(encoding="utf-8")
    assert "import webview" not in source
    assert "create_window" not in source


def test_main_does_not_open_a_window_before_the_screen():
    """The other half: __main__ must not have kept a call to it."""
    source = Path(__file__).resolve().parents[1] / "src" / "__main__.py"
    text = source.read_text(encoding="utf-8")
    assert "ask_capital" not in text


def test_the_warning_names_the_number_and_the_file(settings_mock):
    text = warn_text(settings_mock)
    assert "100,000,000" in text
    assert "configs/user.yaml" in text
    assert "capital_rp" in text


# ------------------------------------------------------------------ the banner
def _brief(**kw):
    from market.regime import Regime, Signal
    from portfolio.fees import FeeConfig, estimate_fees
    from report.brief import render_brief

    defaults = dict(
        regime=Regime([Signal("IHSG trend", "^JKSE", True, "above trend")],
                      1.0, "RISK-ON", "G", "Deploy 100%."),
        orders=[{"action": "BUY", "ticker": "SRTG.JK", "lots": 39, "shares": 3900,
                 "price": 1915.0, "rupiah": 7_468_500, "note": "target weight 25%"}],
        fees=estimate_fees([{"action": "BUY", "rupiah": 7_468_500}], FeeConfig()),
        capital=PLACEHOLDER_CAPITAL, holdings_rows=[], candidates=[], rejected={},
        capped={}, allocation=None, universe_n=49, imputed_n=33,
    )
    defaults.update(kw)
    return render_brief(**defaults)


def test_the_ticket_says_when_it_was_sized_for_the_placeholder():
    out = _brief(placeholder_capital=True)
    assert "placeholder capital, not your money" in out
    assert "Rp100,000,000" in out


def test_a_real_capital_shows_no_banner():
    out = _brief(capital=10_000_000, placeholder_capital=False)
    assert "placeholder capital" not in out


def test_the_banner_sits_with_the_ticket_not_in_a_footnote():
    """It has to be where the lot counts are, or it will not be read."""
    out = _brief(placeholder_capital=True)
    ticket = out.split('id="panel-ticket"')[1].split("</section>")[0]
    assert "placeholder capital, not your money" in ticket
