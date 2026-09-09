"""
Which build wrote this page, and what happens to the ledger when a write goes wrong.

Two unrelated-looking things share a file because they answer the same question:
*can this be recovered afterwards?* A report with no build stamp cannot be traced to
the code that produced it, and a ledger with no backup cannot be recovered at all.

The load-bearing test here is the last one. `keep_a_copy` runs on the path that
records a trade, so a backup that raises would stop you writing down something you
had already done at a broker. It must fail quietly or not at all.
"""
import shutil

import pandas as pd
import pytest

from core.paths import BACKUP_DIR_NAME, keep_a_copy
from core.version import __version__, build_stamp, commit
from portfolio.cash import save_cash
from portfolio.dividends import save_dividends
from portfolio.journal import load_journal, save_journal


@pytest.fixture(autouse=True)
def _uncached():
    """`commit` and `build_stamp` memoise; a test that patches their sources must not
    read an answer computed by an earlier test."""
    commit.cache_clear()
    build_stamp.cache_clear()
    yield
    commit.cache_clear()
    build_stamp.cache_clear()


def _journal(rows):
    return pd.DataFrame(rows, columns=["date", "ticker", "action", "lots", "shares",
                                       "price", "fees_rp", "note", "source"])


# ------------------------------------------------------------------ the stamp
def test_the_stamp_carries_the_version():
    assert __version__ in build_stamp()


def test_the_stamp_survives_having_no_source_at_all():
    """
    Frozen with no `_build.txt` and no git binary. It must still return a string:
    this is decoration on a report, and decoration may not take a run down with it.
    """
    import core.version as v

    original = (v._from_bundle, v._from_git)
    v._from_bundle, v._from_git = (lambda: ""), (lambda: "")
    try:
        commit.cache_clear()
        build_stamp.cache_clear()
        assert commit() == "dev"
        assert build_stamp() == f"v{__version__} (dev)"
    finally:
        v._from_bundle, v._from_git = original


def test_neither_source_raises_when_everything_is_broken(monkeypatch):
    """Both helpers swallow their own failures rather than relying on the caller."""
    import core.version as v

    def explode(*a, **kw):
        raise OSError("no git here")

    monkeypatch.setattr(v.subprocess, "run", explode)
    assert v._from_git() == ""
    assert isinstance(v._from_bundle(), str)


def test_both_reports_say_what_built_them():
    """
    The point of the stamp: a page saved to disk and re-read months later can be
    traced back to the code that wrote its numbers.
    """
    from portfolio.fees import FeeConfig, estimate_fees
    from report.brief import render_brief
    from market.regime import Regime, Signal

    html = render_brief(
        regime=Regime([Signal("IHSG trend", "^JKSE", True, "above trend")],
                      1.0, "RISK-ON", "G", "Deploy 100%."),
        orders=[], fees=estimate_fees([], FeeConfig()), capital=10_000_000,
        holdings_rows=[], candidates=[], rejected={}, capped={}, allocation=None,
        universe_n=49, imputed_n=33,
    )
    assert build_stamp() in html

    from backtest.report import render_html
    assert build_stamp() in render_html({})


# ----------------------------------------------------------------- the backup
def test_the_previous_version_is_recoverable(tmp_path):
    """The whole reason this exists: a ledger write is a whole-file overwrite."""
    path = tmp_path / "journal.csv"
    save_journal(_journal([["2026-01-05", "BBRI.JK", "BUY", 3, 300, 4150.0,
                            0, "", "tool"]]), path)
    save_journal(_journal([]), path)          # the destructive write

    assert load_journal(path).empty, "the live file was overwritten, as expected"
    copies = list((tmp_path / BACKUP_DIR_NAME).glob("journal.*.csv"))
    assert len(copies) == 1
    assert "BBRI.JK" in copies[0].read_text(encoding="utf-8")


def test_the_first_write_of_a_new_ledger_backs_up_nothing(tmp_path):
    """There is no loss to protect against before the file exists."""
    path = tmp_path / "journal.csv"
    assert keep_a_copy(path) is None
    save_journal(_journal([]), path)
    assert not (tmp_path / BACKUP_DIR_NAME).exists()


def test_copies_are_pruned_so_a_weekly_ledger_cannot_fill_a_disk(tmp_path):
    path = tmp_path / "journal.csv"
    path.write_text("seed\n", encoding="utf-8")
    for _ in range(14):
        keep_a_copy(path, keep=10)

    copies = list((tmp_path / BACKUP_DIR_NAME).glob("journal.*.csv"))
    assert len(copies) == 10


def test_copies_made_in_the_same_second_do_not_overwrite_each_other(tmp_path):
    """Second resolution plus a counter, rather than pretending bursts cannot happen."""
    path = tmp_path / "journal.csv"
    path.write_text("seed\n", encoding="utf-8")
    made = {keep_a_copy(path, keep=10) for _ in range(4)}
    assert len(made) == 4 and None not in made


# =========================================================== THE ONE THAT MATTERS
def test_a_backup_that_cannot_be_written_still_lets_you_record_the_trade(
        tmp_path, monkeypatch):
    """
    A full disk must not stop you writing down a trade you have already executed at
    the broker. The backup is the expendable half of this pair, and the failure is
    swallowed on purpose.
    """
    path = tmp_path / "journal.csv"
    save_journal(_journal([["2026-01-05", "BBRI.JK", "BUY", 3, 300, 4150.0,
                            0, "", "tool"]]), path)

    def no_disk(*a, **kw):
        raise OSError("device is full")

    monkeypatch.setattr(shutil, "copyfile", no_disk)
    save_journal(_journal([["2026-02-09", "TLKM.JK", "BUY", 2, 200, 2800.0,
                            0, "", "tool"]]), path)

    assert load_journal(path)["ticker"].tolist() == ["TLKM.JK"]


@pytest.mark.parametrize("save,frame", [
    (save_cash, pd.DataFrame([{"date": "2026-01-05", "kind": "DEPOSIT",
                               "amount_rp": 1_000_000, "note": ""}])),
    (save_dividends, pd.DataFrame([{"date": "2026-01-05", "ticker": "BBRI.JK",
                                    "amount_rp": 50_000, "note": ""}])),
])
def test_every_ledger_is_covered_not_only_the_journal(tmp_path, save, frame):
    """Cash and dividends are reconstructible from nothing too. They get the same guard."""
    path = tmp_path / "ledger.csv"
    save(frame, path)
    save(frame.iloc[0:0], path)
    assert list((tmp_path / BACKUP_DIR_NAME).glob("ledger.*.csv"))
