"""
Tests for the Python bridge the terminal calls.

Two properties carry it:

  * **The preview is the trade.** They must be the same computation, not two that
    agree today. The stamp is Rp10,000 only on the first sell of a day, so a preview
    that did not read the journal would quote it on a second sell and then record
    Rp0 -- and the reader would find out reconciling against their broker weeks
    later.

  * **Nothing raises across the bridge.** An exception surfaces in JavaScript as a
    rejected promise with no message, which on screen is a button that does nothing.
"""
import json

import pandas as pd
import pytest
import yaml

from api import TerminalAPI


@pytest.fixture
def api(settings_mock, tmp_path):
    settings_mock.account = {
        **(settings_mock.account or {}),
        "journal_path": str(tmp_path / "journal.csv"),
        "marks_path": str(tmp_path / "marks.csv"),
        "holdings_path": str(tmp_path / "holdings.yaml"),
        # Every file the API writes belongs under tmp_path. Leaving cash_path at
        # its default sent these tests at the repo's own data/cash.csv, where they
        # accumulated across the whole run and across each other.
        "cash_path": str(tmp_path / "cash.csv"),
        "snapshot_path": str(tmp_path / "run.joblib"),
        "capital_rp": 10_000_000,
    }
    settings_mock.events_path = str(tmp_path / "events.yaml")
    return TerminalAPI(settings_mock, prices={"BBRI.JK": 4200.0})


# ------------------------------------------------- the preview is the trade
def test_preview_and_log_agree_on_every_number(api):
    preview = api.preview_trade("BUY", "BBRI", 3, 4150)
    assert preview["ok"]

    logged = api.log_trade("BUY", "BBRI", 3, 4150)
    assert logged["ok"]

    for field in ("gross_rp", "fee_rp", "stamp_rp", "net_rp", "shares", "ticker"):
        assert preview["data"][field] == logged["data"]["trade"][field], field


def test_the_second_sell_of_a_day_previews_no_stamp(api):
    """
    The case a JavaScript fee preview would get wrong: quote Rp10,000, record Rp0.
    """
    api.log_trade("BUY", "BBRI", 3, 4000, "2026-08-10")
    api.log_trade("BUY", "TLKM", 2, 2600, "2026-08-10")

    first = api.preview_trade("SELL", "BBRI", 3, 4200, "2026-08-20")
    assert first["data"]["stamp_rp"] == 10_000
    api.log_trade("SELL", "BBRI", 3, 4200, "2026-08-20")

    second = api.preview_trade("SELL", "TLKM", 2, 2500, "2026-08-20")
    assert second["data"]["stamp_rp"] == 0, "a second sell the same day is not stamped twice"

    recorded = api.log_trade("SELL", "TLKM", 2, 2500, "2026-08-20")
    assert recorded["data"]["trade"]["stamp_rp"] == 0


def test_a_buy_is_money_out_and_a_sell_is_money_in(api):
    buy = api.preview_trade("BUY", "BBRI", 3, 4150)
    assert buy["data"]["net_rp"] < 0
    api.log_trade("BUY", "BBRI", 3, 4150)
    sell = api.preview_trade("SELL", "BBRI", 3, 4150)
    assert sell["data"]["net_rp"] > 0


def test_logging_writes_a_row_a_spreadsheet_can_read(api, settings_mock):
    api.log_trade("BUY", "BBRI", 3, 4150, note="first buy", source="own")
    df = pd.read_csv(settings_mock.account["journal_path"])
    row = df.iloc[0]
    assert row["ticker"] == "BBRI.JK" and row["action"] == "BUY"
    assert row["lots"] == 3 and row["shares"] == 300
    assert row["note"] == "first buy" and row["source"] == "own"


def test_logging_returns_fresh_panels(api):
    out = api.log_trade("BUY", "BBRI", 3, 4150)
    assert "Realised, by month" in out["data"]["journal_html"]


# ------------------------------------------------------ nothing ever raises
@pytest.mark.parametrize("call", [
    lambda a: a.preview_trade("BUY", "BBRI", 0, 4150),
    lambda a: a.preview_trade("BUY", "BBRI", -2, 4150),
    lambda a: a.preview_trade("BUY", "BBRI", 3, 0),
    lambda a: a.preview_trade("BUY", "BBRI", 3, -50),
    lambda a: a.preview_trade("HOLD", "BBRI", 3, 4150),
    lambda a: a.preview_trade("BUY", "BBRI", "three", 4150),
    lambda a: a.preview_trade("BUY", "BBRI", 3, "cheap"),
    lambda a: a.preview_trade("BUY", "", 3, 4150),
    lambda a: a.log_trade("BUY", "BBRI", 3, 4150, "not-a-date"),
])
def test_bad_input_comes_back_as_a_sentence(api, call):
    out = call(api)
    assert out["ok"] is False
    assert out["message"], "a failure with no message is a button that does nothing"


def test_every_result_is_json_serialisable(api):
    """It crosses into JavaScript; anything pandas leaves behind breaks the call."""
    api.log_trade("BUY", "BBRI", 3, 4150)
    for out in (api.preview_trade("BUY", "BBRI", 1, 4150),
                api.refresh_journal(), api.get_settings()):
        json.dumps(out)


def test_selling_more_than_you_hold_is_refused(api):
    api.log_trade("BUY", "BBRI", 2, 4000)
    out = api.log_trade("SELL", "BBRI", 5, 4200)
    assert out["ok"] is False
    assert "fewer" in out["message"]


def test_the_refused_sell_was_not_written(api, settings_mock):
    api.log_trade("BUY", "BBRI", 2, 4000)
    api.log_trade("SELL", "BBRI", 5, 4200)
    df = pd.read_csv(settings_mock.account["journal_path"])
    assert list(df["action"]) == ["BUY"]


def test_an_unknown_ticker_is_still_recorded(api):
    """
    Deliberate: the universe is a screening list, not a whitelist of what you own.
    Refusing a name outside it would stop you logging a real trade.
    """
    assert api.log_trade("BUY", "GOTO", 10, 55)["ok"] is True


# -------------------------------------------------------------- the settings
def test_settings_show_current_beside_default(api):
    fields = api.get_settings()["data"]["fields"]
    capital = next(f for f in fields if f["path"] == "account.capital_rp")
    assert capital["value"] == 10_000_000
    assert capital["default"] == 100_000_000
    assert capital["overridden"] is True


def test_saving_one_field_keeps_the_others(api, tmp_path, monkeypatch):
    """
    The regression guard for the shallow-merge defect: a settings screen edits one
    field at a time, and the second edit used to drop the first.
    """
    monkeypatch.chdir(tmp_path)
    assert api.save_setting("account.capital_rp", 15_000_000)["ok"]
    assert api.save_setting("account.min_positions", 4)["ok"]

    saved = yaml.safe_load((tmp_path / "configs" / "user.yaml").read_text(encoding="utf-8"))
    assert saved["account"]["capital_rp"] == 15_000_000
    assert saved["account"]["min_positions"] == 4, "the first edit was dropped"


def test_saving_never_touches_default_yaml(api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()
    default = tmp_path / "configs" / "default.yaml"
    default.write_text("account:\n  capital_rp: 100000000\n", encoding="utf-8")
    before = default.read_text(encoding="utf-8")

    api.save_setting("account.capital_rp", 22_000_000)
    assert default.read_text(encoding="utf-8") == before


def test_a_field_that_is_not_editable_is_refused(api):
    out = api.save_setting("factor_weights", {"pe_ratio": 99})
    assert out["ok"] is False and "not editable" in out["message"]


@pytest.mark.parametrize("bad", ["abc", "", None, "12.5.6"])
def test_a_value_that_is_not_a_number_is_refused(api, bad, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = api.save_setting("account.capital_rp", bad)
    assert out["ok"] is False
    assert not (tmp_path / "configs" / "user.yaml").exists(), "a bad value was written"


def test_changing_a_ranking_setting_says_the_page_is_stale(api, tmp_path, monkeypatch):
    """The ranking on screen was built before the edit and cannot re-rank itself."""
    monkeypatch.chdir(tmp_path)
    out = api.save_setting("top_picks_n", 12)
    assert out["ok"] and "re-run" in out["message"]


def test_resetting_removes_the_override_rather_than_writing_the_default(api, tmp_path,
                                                                       monkeypatch):
    """
    Writing the default back would freeze today's value into the user's file, and a
    later change to the shipped default would silently never reach them.
    """
    monkeypatch.chdir(tmp_path)
    api.save_setting("account.min_positions", 4)
    api.save_setting("top_picks_n", 12)
    assert api.reset_setting("top_picks_n")["ok"]

    saved = yaml.safe_load((tmp_path / "configs" / "user.yaml").read_text(encoding="utf-8"))
    assert "top_picks_n" not in saved
    assert saved["account"]["min_positions"] == 4


def test_capital_cannot_be_reset_to_the_placeholder(api, tmp_path, monkeypatch):
    """
    Capital has no sensible default: the shipped figure is a placeholder that sizes
    a Rp30 juta ticket for a Rp10 juta account. A reset button beside it is a
    one-click way back to exactly that.
    """
    monkeypatch.chdir(tmp_path)
    api.save_setting("account.capital_rp", 15_000_000)
    out = api.reset_setting("account.capital_rp")

    assert out["ok"] is False and "no default" in out["message"]
    saved = yaml.safe_load((tmp_path / "configs" / "user.yaml").read_text(encoding="utf-8"))
    assert saved["account"]["capital_rp"] == 15_000_000, "capital was wiped anyway"


def test_resetting_the_last_override_leaves_no_empty_block(api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    api.save_setting("account.min_positions", 5)
    api.reset_setting("account.min_positions")
    saved = yaml.safe_load((tmp_path / "configs" / "user.yaml").read_text(encoding="utf-8")) or {}
    assert "account" not in saved, "an empty block reads as 'something is overridden'"


# ------------------------------------------------------------------- events
def test_an_event_can_be_noted(api, settings_mock):
    out = api.add_event("ADRO", "earnings", "2026-08-27", "Q2")
    assert out["ok"]
    saved = yaml.safe_load(open(settings_mock.events_path, encoding="utf-8"))
    assert any(e["scope"] == "ADRO.JK" for e in saved["events"])


def test_an_incomplete_event_is_refused(api):
    assert api.add_event("", "earnings", "2026-08-27")["ok"] is False
    assert api.add_event("ADRO", "", "2026-08-27")["ok"] is False
    assert api.add_event("ADRO", "earnings", "")["ok"] is False


# --------------------------------- what crosses the bridge must stay simple
def test_the_bridge_object_exposes_only_methods(settings_mock):
    """
    pywebview builds the JS object by walking `dir(obj)`: it skips names starting
    with "_", exposes methods, and RECURSES INTO every other public attribute. A
    public `ctx` holding a DataFrame sent it down `df.T.T.T...` forever and sprayed
    hundreds of errors on every launch. Nothing public but methods.
    """
    import pandas as pd

    from api import TerminalAPI, set_context

    class Ctx:
        df = pd.DataFrame({"a": [1, 2]})
        args = None

    set_context(Ctx())
    api = TerminalAPI(settings_mock)

    public = [n for n in dir(api) if not n.startswith("_")]
    assert public, "the bridge exposes nothing at all"
    for name in public:
        assert callable(getattr(api, name)), (
            f"{name!r} is a public non-callable; pywebview will recurse into it"
        )
    set_context(None)


def test_the_context_is_still_reachable_internally(settings_mock):
    from api import TerminalAPI, set_context

    marker = object()
    set_context(marker)
    assert TerminalAPI(settings_mock)._ctx is marker
    set_context(None)


def test_rebuild_without_a_context_says_so(settings_mock):
    from api import TerminalAPI, set_context

    set_context(None)
    out = TerminalAPI(settings_mock).rebuild()
    assert out["ok"] is False and "restart" in out["message"]


# ============================================================================
# The crash: navigating the window from inside an API call.
# ============================================================================
class _NoNavWebview:
    """A webview whose load_url fails the test if anything reaches for it."""

    def __init__(self):
        self.windows = [self]

    def load_url(self, *a, **k):
        raise AssertionError(
            "an API method navigated the window. pywebview delivers a call's reply "
            "with evaluate_js OUTSIDE its own try/except, and that waits for the page "
            "to be loaded - navigating mid-call throws on pywebview's thread and "
            "leaves the JS promise unsettled, which killed every button."
        )


@pytest.fixture
def ctx_api(settings_mock, tmp_path):
    """An API with a real render context behind it, so rebuild actually works."""
    import numpy as np

    from api import TerminalAPI, set_context
    from market.regime import Regime, Signal
    from runner import RunContext

    tickers = list(settings_mock.stock_tickers)
    df = pd.DataFrame([{
        "ticker": t, "name": t, "sector": settings_mock.sectors[t],
        "undervaluation_score": 1.0 - i * 0.1, "composite_score": 1.0 - i * 0.1,
        "raw_score": 1.0 - i * 0.1, "last_close": 1000.0 + i * 100,
        "median_daily_value_rp": 5e9, "imputed_factors": "",
        "pe_ratio": 10.0 + i, "price_to_book": 1.0 + i * 0.1, "roe": 0.15,
        "unclipped_pe_ratio": 10.0 + i, "unclipped_price_to_book": 1.0 + i * 0.1,
    } for i, t in enumerate(tickers)])

    idx = pd.date_range("2025-01-01", periods=300, freq="D")
    bench = pd.DataFrame({c: np.linspace(6000, 6500, 300)
                          for c in ("Open", "High", "Low", "Close")}, index=idx)
    bench["Volume"] = 1e9

    settings_mock.output_dir = tmp_path / "out"
    settings_mock.output_dir.mkdir(parents=True, exist_ok=True)
    settings_mock.account = {
        **settings_mock.account,
        "journal_path": str(tmp_path / "journal.csv"),
        "marks_path": str(tmp_path / "marks.csv"),
        "holdings_path": str(tmp_path / "holdings.yaml"),
        # Every file the API writes belongs under tmp_path. Leaving cash_path at
        # its default sent these tests at the repo's own data/cash.csv, where they
        # accumulated across the whole run and across each other.
        "cash_path": str(tmp_path / "cash.csv"),
        "snapshot_path": str(tmp_path / "run.joblib"),
        "capital_rp": 10_000_000,
    }
    settings_mock.events_path = str(tmp_path / "events.yaml")

    class Args:
        browser = True
        png = False

    set_context(RunContext(
        settings=settings_mock, args=Args(), df=df, price_data={},
        benchmark_data={"^JKSE": bench},
        regime=Regime([Signal("IHSG trend", "^JKSE", True, "up")], 1.0,
                      "RISK-ON", "G", "Deploy 100%."),
    ))
    yield TerminalAPI(settings_mock, prices={t: 1000.0 for t in tickers})
    set_context(None)


def test_rebuild_does_not_navigate_the_window(ctx_api, monkeypatch):
    """The regression guard for the WebViewException that killed every button."""
    import sys
    monkeypatch.setitem(sys.modules, "webview", _NoNavWebview())
    out = ctx_api.rebuild()
    assert out["ok"], out["message"]


def test_rebuild_hands_back_a_url_for_the_page_to_follow(ctx_api):
    out = ctx_api.rebuild()
    assert out["ok"]
    assert out["data"]["url"].startswith("file:")
    assert out["data"]["url"].endswith(".html")


def test_recording_a_trade_returns_a_url_so_the_whole_page_refreshes(ctx_api):
    """
    Swapping only the ledger left the KPIs and the top bar reading zero next to a
    ledger that visibly had a trade in it.
    """
    out = ctx_api.log_trade("BUY", list(ctx_api._settings.stock_tickers)[0], 3, 1000)
    assert out["ok"]
    assert out["data"].get("url"), "the page has nothing to navigate to"


def test_a_second_trade_records(ctx_api):
    """The reported symptom: one record worked, the next did nothing."""
    ticker = list(ctx_api._settings.stock_tickers)[0]
    assert ctx_api.log_trade("BUY", ticker, 3, 1000)["ok"]
    assert ctx_api.log_trade("BUY", ticker, 2, 1100)["ok"]

    rows = pd.read_csv(ctx_api._settings.account["journal_path"])
    assert len(rows) == 2, f"only {len(rows)} row(s) written"


# ------------------------------------------------------------ undo a mistake
def test_the_last_trade_can_be_named_before_it_is_removed(ctx_api):
    ticker = list(ctx_api._settings.stock_tickers)[0]
    ctx_api.log_trade("BUY", ticker, 3, 125)
    out = ctx_api.last_trade()
    assert out["ok"] and ticker in out["message"] and "125" in out["message"]


def test_removing_the_last_trade_undoes_exactly_it(ctx_api):
    """A price typed wrong is otherwise permanent, and +1429% sits there forever."""
    ticker = list(ctx_api._settings.stock_tickers)[0]
    ctx_api.log_trade("BUY", ticker, 3, 1900, "2026-08-01")
    ctx_api.log_trade("BUY", ticker, 1, 125, "2026-08-02")     # the typo

    assert ctx_api.remove_last_trade()["ok"]
    rows = pd.read_csv(ctx_api._settings.account["journal_path"])
    assert len(rows) == 1
    assert float(rows.iloc[0]["price"]) == 1900.0, "it removed the wrong row"


def test_removing_from_an_empty_journal_is_refused(ctx_api):
    out = ctx_api.remove_last_trade()
    assert out["ok"] is False and "nothing to remove" in out["message"].lower()


def test_last_trade_on_an_empty_journal_says_so(ctx_api):
    assert "Nothing recorded" in ctx_api.last_trade()["message"]


# --------------------------------------------------- catching a bad entry
# The trade that started all of this. A Rp125 buy for a stock trading near Rp1,900
# went in unquestioned, and the resulting +1412% round-trip read as a result.
def test_a_price_far_from_the_last_close_is_flagged(api):
    r = api.preview_trade("BUY", "BBRI", 1, 300)      # close is 4,200
    assert r["ok"]
    assert "93% below" in r["message"]
    assert "4,200" in r["message"]


def test_a_price_near_the_last_close_is_not_flagged(api):
    assert api.preview_trade("BUY", "BBRI", 1, 4100)["message"] == ""


def test_an_odd_price_is_still_allowed_to_be_recorded(api):
    """A warning, not a veto: some names have no price and some gaps are real."""
    assert api.log_trade("BUY", "BBRI", 1, 300)["ok"]


def test_a_ticker_with_no_known_price_is_not_flagged(api):
    assert api.preview_trade("BUY", "ZZZZ", 1, 1)["message"] == ""


# ------------------------------------------------------------- break even
def test_break_even_is_above_the_buy_price_by_every_cost(api):
    """
    Buy one lot at 1,925: 192,865.75 out with the fee. Selling has to clear the
    0.29% sell fee and the Rp10,000 stamp on top, so 2,000 is not enough.
    """
    d = api.preview_trade("BUY", "SRTG", 1, 1925)["data"]
    assert d["break_even"] == pytest.approx(2034.56, abs=0.01)
    assert round(d["break_even"]) == 2035
    assert d["break_even_move_pct"] == pytest.approx(5.69, abs=0.01)


def test_the_trade_that_looked_like_a_win_actually_loses(api):
    """
    Pinned by hand. Buy 1,925, sell 2,000, one lot: a 75-point gain that loses
    Rp3,446, because the stamp alone is larger than the Rp7,500 of profit.
    """
    buy = api.log_trade("BUY", "SRTG", 1, 1925, "2026-08-21")["data"]["trade"]
    sell = api.log_trade("SELL", "SRTG", 1, 2000, "2026-08-22")["data"]["trade"]

    assert buy["fee_rp"] == 365.75
    assert sell["fee_rp"] == 580.0 and sell["stamp_rp"] == 10_000
    assert sell["net_rp"] == pytest.approx(189_420.0, abs=0.01)
    # The stamp is Rp10,000 against Rp7,500 of gross gain. It cannot come out ahead.
    assert sell["net_rp"] + buy["net_rp"] == pytest.approx(-3445.75, abs=0.01)


def test_a_sell_has_no_break_even_of_its_own(api):
    api.log_trade("BUY", "BBRI", 1, 4000)
    assert "break_even" not in api.preview_trade("SELL", "BBRI", 1, 4200)["data"]


# ------------------------------------------------- what a sell would close
def test_a_sell_preview_names_the_lot_it_consumes(api):
    """
    "This closes 1 lot bought at Rp125" is the sentence that catches the typo while
    it is still fixable -- before the sale, not in the ledger afterwards.
    """
    api.log_trade("BUY", "SRTG", 1, 125, "2026-08-20")
    note = api.preview_trade("SELL", "SRTG", 1, 2000, "2026-08-22")["data"]["match_note"]
    assert "1 lot bought at Rp125" in note
    assert "20 Aug" in note


def test_selling_what_is_not_held_says_so(api):
    note = api.preview_trade("SELL", "BBRI", 1, 4200)["data"]["match_note"]
    assert "no open" in note.lower()


# ----------------------------------------------------- removal through the bridge
def test_the_bridge_removes_the_row_it_was_shown(api):
    api.log_trade("BUY", "SRTG", 1, 125, "2026-08-20")
    api.log_trade("BUY", "BBRI", 2, 4150, "2026-08-21")

    rows = api.list_trades()["data"]["trades"]
    typo = next(r for r in rows if r["price"] == 125)

    out = api.remove_trade(typo["index"], typo["ticker"], typo["price"], typo["date"])
    assert out["ok"], out["message"]

    left = [r["ticker"] for r in api.list_trades()["data"]["trades"]]
    assert left == ["BBRI.JK"]


def test_the_bridge_refuses_a_row_that_has_moved(api):
    api.log_trade("BUY", "SRTG", 1, 125, "2026-08-20")
    api.log_trade("BUY", "BBRI", 2, 4150, "2026-08-21")
    assert not api.remove_trade(1, "SRTG.JK", 125, "2026-08-20")["ok"]
    assert len(api.list_trades()["data"]["trades"]) == 2


def test_removal_never_raises_across_the_bridge(api):
    for bad in [("x", "", None, ""), (99, "", None, ""), (0, "", None, "")]:
        assert api.remove_trade(*bad)["ok"] is False


def test_the_previewed_realisation_matches_what_gets_recorded(api):
    """
    Across several lots at different prices, and a partial one. The preview is what
    the decision is made on, so it may not be an approximation of its own outcome.
    """
    from portfolio.journal import closed_trades, load_journal

    api.log_trade("BUY", "BBRI", 2, 4000, "2026-08-10")
    api.log_trade("BUY", "BBRI", 3, 4400, "2026-08-11")

    note = api.preview_trade("SELL", "BBRI", 4, 4600, "2026-08-20")["data"]["match_note"]
    assert "2 lot bought at Rp4,000" in note and "2 lot bought at Rp4,400" in note

    previewed = float(note.split("Rp")[-1].rstrip(".").replace(",", ""))
    api.log_trade("SELL", "BBRI", 4, 4600, "2026-08-20")

    journal_path, _, _ = api._paths()
    realised = closed_trades(load_journal(journal_path))["net_pnl"].sum()
    assert previewed == pytest.approx(realised, abs=1.0)


# ------------------------------------------------------------ cash in and out
# Capital comes from this ledger now. It used to be a number typed into a config
# file that a rebuild of the app kept deleting.
def test_a_deposit_sets_capital(api):
    r = api.record_cash("DEPOSIT", 10_000_000, "2026-08-12", "opening balance")
    assert r["ok"], r["message"]
    assert r["data"]["capital"] == 10_000_000
    assert api._settings.capital_rp == 10_000_000
    assert "10,000,000" in r["message"]


def test_a_withdrawal_reduces_it(api):
    api.record_cash("DEPOSIT", 10_000_000, "2026-08-12")
    r = api.record_cash("WITHDRAW", 2_000_000, "2026-08-20")
    assert r["data"]["capital"] == 8_000_000


def test_taking_out_more_than_was_paid_in_is_refused_and_records_nothing(api):
    api.record_cash("DEPOSIT", 5_000_000, "2026-08-12")
    r = api.record_cash("WITHDRAW", 6_000_000, "2026-08-20")

    assert not r["ok"]
    assert "cannot come out" in r["message"]
    assert api._settings.capital_rp == 5_000_000
    assert len(api.list_cash()["data"]["entries"]) == 1


def test_the_preview_says_what_capital_would_become(api):
    api.record_cash("DEPOSIT", 10_000_000, "2026-08-12")
    d = api.preview_cash("DEPOSIT", 5_000_000, "2026-08-20")["data"]
    assert d["capital_now"] == 10_000_000
    assert d["capital_after"] == 15_000_000


def test_withdrawing_more_than_is_free_warns_but_still_records(api):
    """
    Your broker is the authority on what has settled; this tool only knows the
    trades you have told it about. So it says so rather than refusing.
    """
    api.record_cash("DEPOSIT", 10_000_000, "2026-08-12")
    api.log_trade("BUY", "BBRI", 20, 4_150, "2026-08-13")     # 8.3 juta committed

    warned = api.preview_cash("WITHDRAW", 5_000_000, "2026-08-20")
    assert warned["ok"]
    assert "more than" in warned["message"]
    assert api.record_cash("WITHDRAW", 5_000_000, "2026-08-20")["ok"]


def test_free_cash_counts_the_trades_as_well_as_the_deposits(api):
    api.record_cash("DEPOSIT", 10_000_000, "2026-08-12")
    api.log_trade("BUY", "BBRI", 1, 4_000, "2026-08-13")      # 400,000 + fee out

    d = api.preview_cash("WITHDRAW", 1_000_000, "2026-08-20")["data"]
    assert d["free_now"] == pytest.approx(10_000_000 - 400_760, abs=1)


def test_entries_are_listed_with_the_index_removal_takes(api):
    api.record_cash("DEPOSIT", 10_000_000, "2026-08-12")
    api.record_cash("DEPOSIT", 3_000_000, "2026-08-15")

    entries = api.list_cash()["data"]["entries"]
    assert [e["amount_rp"] for e in entries] == [10_000_000, 3_000_000]
    assert [e["index"] for e in entries] == [0, 1]


def test_removing_an_entry_re_derives_capital(api):
    api.record_cash("DEPOSIT", 10_000_000, "2026-08-12")
    api.record_cash("DEPOSIT", 3_000_000, "2026-08-15")

    r = api.remove_cash(1, "DEPOSIT", 3_000_000, "2026-08-15")
    assert r["ok"], r["message"]
    assert r["data"]["capital"] == 10_000_000
    assert api._settings.capital_rp == 10_000_000


def test_the_bridge_refuses_a_cash_row_that_has_moved(api):
    api.record_cash("DEPOSIT", 10_000_000, "2026-08-12")
    api.record_cash("DEPOSIT", 3_000_000, "2026-08-15")

    assert not api.remove_cash(1, "DEPOSIT", 10_000_000, "2026-08-12")["ok"]
    assert len(api.list_cash()["data"]["entries"]) == 2


def test_nothing_in_the_cash_bridge_raises(api):
    for bad in [("", 0, "", ""), ("DEPOSIT", "abc", "", ""), ("SPEND", 1, "", "")]:
        assert api.record_cash(*bad)["ok"] is False
        assert api.preview_cash(bad[0], bad[1], bad[2])["ok"] is False
    assert api.remove_cash("x")["ok"] is False
    assert api.list_cash()["ok"] is True


def test_capital_stays_editable_until_there_is_a_ledger(api):
    assert api.save_setting("account.capital_rp", 7_500_000)["ok"]
    assert api._settings.capital_rp == 7_500_000


def test_once_there_is_a_ledger_the_settings_field_refuses(api):
    """Two numbers describing the same money would be free to disagree."""
    api.record_cash("DEPOSIT", 10_000_000, "2026-08-12")

    r = api.save_setting("account.capital_rp", 99_000_000)
    assert not r["ok"]
    assert "cash ledger" in r["message"]
    assert api._settings.capital_rp == 10_000_000
