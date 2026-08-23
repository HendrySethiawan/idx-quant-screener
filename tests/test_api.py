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
    api.save_setting("account.capital_rp", 15_000_000)
    api.save_setting("account.min_positions", 4)
    assert api.reset_setting("account.capital_rp")["ok"]

    saved = yaml.safe_load((tmp_path / "configs" / "user.yaml").read_text(encoding="utf-8"))
    assert "capital_rp" not in saved.get("account", {})
    assert saved["account"]["min_positions"] == 4


def test_resetting_the_last_override_leaves_no_empty_block(api, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    api.save_setting("account.capital_rp", 15_000_000)
    api.reset_setting("account.capital_rp")
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
