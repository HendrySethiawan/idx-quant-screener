"""
Deposits, withdrawals, and the capital they define.

Capital used to be a number typed into `configs/user.yaml`. That put the figure
every recommendation is sized against in a different place from the money it
described, with nothing keeping them honest -- and it lived in a folder the build
deleted, so it kept vanishing. Here it has a date, a reason, and one definition.

The load-bearing test is test_capital_is_what_you_have_paid_in: everything
downstream -- lot sizing, the ticket, fee estimates, the backtest -- reads
`settings.capital_rp`, so if that does not follow the ledger, nothing else can.
"""
from pathlib import Path

import pandas as pd
import pytest

from portfolio import cash as C


def _ledger(tmp_path, rows):
    """rows: (kind, amount, date)"""
    path = tmp_path / "cash.csv"
    for kind, amount, when in rows:
        C.append_entry(C.build_entry(kind, amount, when), path)
    return path


# ------------------------------------------------------------------ building
def test_a_deposit_is_recorded_with_its_date_and_reason():
    entry = C.build_entry("DEPOSIT", 10_000_000, "2026-08-12", "opening balance")
    assert entry["kind"] == "DEPOSIT"
    assert entry["amount_rp"] == 10_000_000
    assert entry["date"] == "2026-08-12"
    assert entry["note"] == "opening balance"


def test_the_kind_carries_the_sign_not_the_number():
    """
    A negative deposit and a withdrawal would be the same row written two ways, and
    only one of them reads correctly in the ledger.
    """
    with pytest.raises(ValueError):
        C.build_entry("DEPOSIT", -1_000_000)
    with pytest.raises(ValueError):
        C.build_entry("WITHDRAW", -1_000_000)


def test_zero_is_not_a_movement():
    with pytest.raises(ValueError):
        C.build_entry("DEPOSIT", 0)


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        C.build_entry("TRANSFER", 1_000_000)


def test_a_price_shaped_string_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        C.build_entry("DEPOSIT", "ten million")


# -------------------------------------------------------------- paid in, net
def test_deposits_minus_withdrawals(tmp_path):
    path = _ledger(tmp_path, [
        ("DEPOSIT", 10_000_000, "2026-08-12"),
        ("WITHDRAW", 2_000_000, "2026-08-20"),
    ])
    assert C.net_paid_in(C.load_cash(path)) == 8_000_000


def test_an_empty_ledger_is_zero_not_an_error(tmp_path):
    assert C.net_paid_in(C.load_cash(tmp_path / "nothing.csv")) == 0.0
    assert C.net_paid_in(None) == 0.0


def test_totals_separate_what_went_in_from_what_came_out(tmp_path):
    path = _ledger(tmp_path, [
        ("DEPOSIT", 10_000_000, "2026-08-12"),
        ("DEPOSIT", 3_000_000, "2026-08-15"),
        ("WITHDRAW", 2_000_000, "2026-08-20"),
    ])
    t = C.totals(C.load_cash(path))
    assert t["deposits"] == 13_000_000
    assert t["withdrawals"] == 2_000_000
    assert t["net"] == 11_000_000
    assert t["entries"] == 3


def test_a_corrupt_file_degrades_quietly(tmp_path):
    path = tmp_path / "cash.csv"
    path.write_text("this is not a csv at all\x00\x00", encoding="utf-8")
    assert C.load_cash(path).empty


def test_rows_without_an_amount_are_dropped_rather_than_counted_as_zero(tmp_path):
    path = tmp_path / "cash.csv"
    path.write_text("date,kind,amount_rp,note\n"
                    "2026-08-12,DEPOSIT,10000000,\n"
                    "2026-08-13,DEPOSIT,,broken\n", encoding="utf-8")
    assert len(C.load_cash(path)) == 1
    assert C.net_paid_in(C.load_cash(path)) == 10_000_000


# ------------------------------------------------------------- taking it out
def test_taking_out_more_than_was_ever_paid_in_is_refused(tmp_path):
    """Not a judgement about affordability -- an arithmetic impossibility."""
    path = _ledger(tmp_path, [("DEPOSIT", 5_000_000, "2026-08-12")])
    ledger = C.load_cash(path)
    assert C.would_overdraw(ledger, C.build_entry("WITHDRAW", 6_000_000))
    assert not C.would_overdraw(ledger, C.build_entry("WITHDRAW", 5_000_000))


def test_a_deposit_never_overdraws(tmp_path):
    assert not C.would_overdraw(pd.DataFrame(), C.build_entry("DEPOSIT", 1))


# ---------------------------------------------------------------- removal
def test_any_entry_can_be_removed(tmp_path):
    path = _ledger(tmp_path, [
        ("DEPOSIT", 10_000_000, "2026-08-12"),
        ("DEPOSIT", 3_000_000, "2026-08-15"),
    ])
    out = C.remove_entry_at(path, 0)
    assert out["ok"], out["message"]
    assert C.net_paid_in(C.load_cash(path)) == 3_000_000


def test_removing_a_deposit_a_withdrawal_depends_on_is_refused(tmp_path):
    """It would leave more taken out than was ever put in, which cannot be true."""
    path = _ledger(tmp_path, [
        ("DEPOSIT", 10_000_000, "2026-08-12"),
        ("WITHDRAW", 8_000_000, "2026-08-20"),
    ])
    out = C.remove_entry_at(path, 0)
    assert not out["ok"]
    assert "paid in" in out["message"]
    assert len(C.load_cash(path)) == 2      # nothing was written


def test_the_withdrawal_can_still_be_removed_first(tmp_path):
    path = _ledger(tmp_path, [
        ("DEPOSIT", 10_000_000, "2026-08-12"),
        ("WITHDRAW", 8_000_000, "2026-08-20"),
    ])
    assert C.remove_entry_at(path, 1)["ok"]
    assert C.remove_entry_at(path, 0)["ok"]
    assert C.load_cash(path).empty


def test_a_row_that_is_not_the_one_displayed_is_refused(tmp_path):
    """The page may have been rebuilt since the button was drawn."""
    path = _ledger(tmp_path, [
        ("DEPOSIT", 10_000_000, "2026-08-12"),
        ("DEPOSIT", 3_000_000, "2026-08-15"),
    ])
    stale = C.remove_entry_at(path, 1, {"kind": "DEPOSIT", "amount_rp": 10_000_000})
    assert not stale["ok"]
    assert len(C.load_cash(path)) == 2

    assert C.remove_entry_at(path, 0, {"kind": "DEPOSIT",
                                       "amount_rp": 10_000_000})["ok"]


def test_removing_out_of_range_says_so_rather_than_raising(tmp_path):
    path = _ledger(tmp_path, [("DEPOSIT", 10_000_000, "2026-08-12")])
    assert not C.remove_entry_at(path, 7)["ok"]
    assert not C.remove_entry_at(path, "x")["ok"]
    assert not C.remove_entry_at(tmp_path / "none.csv", 0)["ok"]


# ------------------------------------------------------------------ capital
def test_capital_is_what_you_have_paid_in(settings_mock, tmp_path):
    """
    Everything downstream reads `settings.capital_rp`. This is the seam.
    """
    path = _ledger(tmp_path, [("DEPOSIT", 10_000_000, "2026-08-12")])
    settings_mock.account = {**settings_mock.account, "cash_path": str(path)}

    assert C.sync_capital(settings_mock) == 10_000_000
    assert settings_mock.capital_rp == 10_000_000


def test_a_withdrawal_reduces_capital(settings_mock, tmp_path):
    path = _ledger(tmp_path, [
        ("DEPOSIT", 10_000_000, "2026-08-12"),
        ("WITHDRAW", 2_000_000, "2026-08-20"),
    ])
    settings_mock.account = {**settings_mock.account, "cash_path": str(path)}

    C.sync_capital(settings_mock)
    assert settings_mock.capital_rp == 8_000_000


def test_no_ledger_leaves_the_configured_capital_alone(settings_mock, tmp_path):
    """
    Anyone who has not recorded a deposit yet must keep the old behaviour:
    user.yaml, then the shipped placeholder.
    """
    settings_mock.account = {**settings_mock.account,
                             "capital_rp": 7_500_000,
                             "cash_path": str(tmp_path / "none.csv")}

    assert C.sync_capital(settings_mock) is None
    assert settings_mock.capital_rp == 7_500_000


def test_syncing_does_not_disturb_the_other_account_settings(settings_mock, tmp_path):
    path = _ledger(tmp_path, [("DEPOSIT", 10_000_000, "2026-08-12")])
    settings_mock.account = {**settings_mock.account,
                             "cash_path": str(path), "min_positions": 4}

    C.sync_capital(settings_mock)
    assert settings_mock.account["min_positions"] == 4
    assert settings_mock.account["cash_path"] == str(path)


def test_the_placeholder_banner_goes_away_once_money_is_recorded(settings_mock, tmp_path):
    from first_run import is_placeholder_capital

    settings_mock.account = {**settings_mock.account, "capital_rp": 100_000_000}
    assert is_placeholder_capital(settings_mock)

    path = _ledger(tmp_path, [("DEPOSIT", 10_000_000, "2026-08-12")])
    settings_mock.account = {**settings_mock.account, "cash_path": str(path)}
    C.sync_capital(settings_mock)

    assert not is_placeholder_capital(settings_mock)


def test_cash_path_follows_settings_like_every_other_file(settings_mock):
    settings_mock.account = {**settings_mock.account, "cash_path": "somewhere/x.csv"}
    assert C.cash_path(settings_mock) == Path("somewhere/x.csv")
