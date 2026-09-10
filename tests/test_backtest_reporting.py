"""
The backtest's tables, and the two questions they used to leave unasked.

`exit_report`, `ablation_report` and `render_html` had no direct coverage at all,
which is how both of these survived:

**The ablation never reached the page.** `cmd_backtest` computed it, put it in
`sections[label]["ablation"]`, printed it to the console -- and `render_html` had no
block for it. Every verdict and every half-window figure was missing from the one
artefact anybody re-reads, and the console scrolls away.

**The stop widths had no stability column.** Section 4 put a twelve-point CAGR gap
between two stop settings in front of a reader with nothing saying whether five years
of one market could resolve it. That is the shape of the mistake the whole stability
apparatus exists to prevent, reproduced one table lower down.
"""
import pandas as pd
import pytest

from backtest.report import exit_report, exit_verdict, render_html

VERDICTS = ("distinguishable", "unstable - concentrated in one half",
            "unstable - reverses between halves", "never binds",
            "direction stable, size unknown", "cannot tell")


def _ablation(rows=None):
    return pd.DataFrame(rows if rows is not None else [
        {"removing": "regime ladder", "cagr_effect_pp": 29.8, "first_half_pp": 2.8,
         "second_half_pp": 63.4, "drawdown_pp": -8.1,
         "verdict": "unstable - concentrated in one half"},
        {"removing": "stops + profit ladder", "cagr_effect_pp": 16.0,
         "first_half_pp": 8.4, "second_half_pp": 24.0, "drawdown_pp": -12.3,
         "verdict": "distinguishable"},
        {"removing": "decorrelation", "cagr_effect_pp": 0.0, "first_half_pp": 0.0,
         "second_half_pp": 0.0, "drawdown_pp": 0.0, "verdict": "never binds"},
    ])


def _exits(with_verdicts=True):
    rows = [
        {"variant": "Hold to the rebalance (no stops)", "cagr_pct": 31.2,
         "max_drawdown_pct": -26.7, "sharpe": 1.16, "fees_paid_rp": 4_287_505,
         "sell_days": 167},
        {"variant": "Stop + ladder, 2.5x ATR (shipped)", "cagr_pct": 14.6,
         "max_drawdown_pct": -14.5, "sharpe": 0.59, "fees_paid_rp": 4_115_794,
         "sell_days": 238},
    ]
    if with_verdicts:
        rows[0].update(first_half_pp=5.0, second_half_pp=30.0,
                       verdict="unstable - concentrated in one half")
        # The shipped row is the baseline and says nothing about itself. Left as
        # None on purpose: pandas turns that into NaN beside a string, and NaN is
        # truthy, which is exactly what broke the first version of the renderer.
        rows[1].update(first_half_pp=None, second_half_pp=None, verdict=None)
    return pd.DataFrame(rows)


def _sections(**over):
    s = {"factors": [],
         "costs": pd.DataFrame(columns=["item", "total_return_pct", "effect_pp",
                                        "kind", "detail"]),
         "regimes": pd.DataFrame(columns=["setting", "cagr_pct", "max_drawdown_pct",
                                          "sharpe"]),
         "robustness": pd.DataFrame(columns=["variant", "cagr_pct",
                                             "max_drawdown_pct", "sharpe"]),
         "exits": _exits(), "ablation": _ablation(), "verdict": "held up",
         "n_rebalances": 260, "avg_names": 74, "fees_paid": 4_115_794}
    s.update(over)
    return {"Weekly": s}


# ================================================= the ablation reaches the page
def test_the_ablation_is_rendered_not_only_printed():
    """
    The defect: this table was computed, printed, and then dropped on the floor.
    Everything below asserts against the HTML because the console is not the artefact.
    """
    html = render_html(_sections())
    assert "What is each part worth" in html
    for row in ("regime ladder", "stops + profit ladder", "decorrelation"):
        assert row in html
    for verdict in ("unstable - concentrated in one half", "distinguishable",
                    "never binds"):
        assert verdict in html


def test_the_ablation_carries_both_halves_not_only_the_total():
    """A single CAGR is the number that made the regime ladder look removable."""
    html = render_html(_sections())
    assert "1st half" in html and "2nd half" in html
    assert "+2.8%" in html and "+63.4%" in html


def test_the_verdict_is_coloured_by_what_it_says():
    html = render_html(_sections())
    assert 'pill good' in html, "a distinguishable result is not marked"
    assert 'pill warn' in html, "an unstable result is not marked"


def test_an_unstable_result_is_never_coloured_as_a_failure():
    """
    `warn`, never `bad`. An effect living in one half is unproven, not proven
    harmful, and red would push the reader to the opposite conclusion just as
    confidently as the bare number used to.
    """
    html = render_html(_sections(ablation=_ablation([
        {"removing": "regime ladder", "cagr_effect_pp": 29.8, "first_half_pp": 2.8,
         "second_half_pp": 63.4, "drawdown_pp": -8.1,
         "verdict": "unstable - concentrated in one half"}])))
    marked = html.split("What is each part worth")[1]
    assert "pill warn" in marked and "pill bad" not in marked


@pytest.mark.parametrize("empty", [pd.DataFrame(), None])
def test_a_cadence_with_no_ablation_renders_without_it(empty):
    """
    Real, not hypothetical: the ablation runs for one cadence only, so the Monthly
    section legitimately carries an empty frame and must not take the page down.
    """
    html = render_html(_sections(ablation=empty))
    assert "What is each part worth" not in html
    assert "Weekly rebalance" in html


def test_the_baseline_row_renders_as_a_dash_not_as_nan():
    """
    NaN is truthy, so `if not verdict` waved it straight through to `.startswith`.
    The shipped row is the only row that hits this, and it is on every real page.
    """
    html = render_html(_sections())
    assert "nan" not in html.lower()
    assert "&mdash;" in html


def test_the_console_grades_the_stop_widths_too():
    """
    The terminal is what most runs are read in, and it scrolls past faster than a
    page does. It gets the same verdicts, in a separate block rather than three more
    columns on a line that is already eighty characters wide.
    """
    from backtest.report import console_block

    out = console_block(
        [], pd.DataFrame(columns=["item", "total_return_pct", "effect_pp", "kind",
                                  "detail"]),
        pd.DataFrame(columns=["setting", "cagr_pct", "max_drawdown_pct", "sharpe"]),
        pd.DataFrame(columns=["variant", "cagr_pct", "max_drawdown_pct", "sharpe"]),
        "held up", "Weekly", 74, None, _exits(), pd.DataFrame())

    assert "would changing to it be real?" in out
    assert "unstable - concentrated in one half" in out
    # No HTML entity may reach a terminal: it prints as the literal characters.
    assert "&mdash;" not in out


def test_a_table_with_no_verdicts_prints_no_stability_block():
    """An older exits frame, or one cadence short of a comparison."""
    from backtest.report import console_block

    out = console_block(
        [], pd.DataFrame(columns=["item", "total_return_pct", "effect_pp", "kind",
                                  "detail"]),
        pd.DataFrame(columns=["setting", "cagr_pct", "max_drawdown_pct", "sharpe"]),
        pd.DataFrame(columns=["variant", "cagr_pct", "max_drawdown_pct", "sharpe"]),
        "held up", "Weekly", 74, None, _exits(with_verdicts=False), pd.DataFrame())

    assert "DO THE STOPS" in out
    assert "would changing to it be real?" not in out


# ============================================ the stop widths get a stability read
def _tiny_panel(n=260, seed=0):
    import numpy as np
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-04", periods=n)
    return pd.DataFrame(
        {t: 1000 * np.cumprod(1 + rng.normal(m, 0.02, n))
         for t, m in (("A.JK", 0.0012), ("B.JK", 0.0006),
                      ("C.JK", 0.0), ("D.JK", -0.0004))}, index=idx)


def test_every_stop_width_is_measured_against_the_one_you_run(monkeypatch):
    """
    The pairing that makes the column mean anything. A reader deciding whether to
    widen their stop is comparing against **what they currently run**, not against
    holding -- so that is the curve every row is paired with, and getting it wrong
    would answer a question nobody asked.
    """
    import backtest.stats as stats

    seen = []
    real = stats.verdict

    def spy(a, b, ppy=52):
        seen.append((id(a), id(b)))
        return real(a, b, ppy)

    monkeypatch.setattr(stats, "verdict", spy)

    panel = _tiny_panel()
    from backtest.engine import BacktestConfig
    from portfolio.fees import FeeConfig
    out = exit_report(panel, 10_000_000, BacktestConfig(rebalance="M"),
                      FeeConfig(), {}, None, None)

    assert not out.empty
    # One baseline, and it is the same object on every comparison.
    baselines = {a for a, _ in seen}
    assert len(baselines) == 1, "rows were paired against different baselines"
    assert len(seen) == len(out) - 1, "every row but the baseline gets a verdict"


def test_the_shipped_row_reports_nothing_about_itself():
    panel = _tiny_panel()
    from backtest.engine import BacktestConfig
    from portfolio.fees import FeeConfig
    out = exit_report(panel, 10_000_000, BacktestConfig(rebalance="M"),
                      FeeConfig(), {}, None, None)

    shipped = out[out["variant"].str.contains("shipped")]
    assert len(shipped) == 1
    # `pd.isna`, not `is None`: a None beside strings comes back as NaN, and that
    # difference is the whole reason the renderer needed an isinstance check.
    assert pd.isna(shipped.iloc[0]["verdict"])
    others = out[~out["variant"].str.contains("shipped")]
    assert others["verdict"].notna().all(), "a variant was left unadjudicated"
    assert set(others["verdict"]) <= set(VERDICTS)


def test_the_shipped_variant_is_found_by_identity_not_by_its_label():
    """
    The label carries the configured `k_atr`, so matching on "2.5x" would silently
    stop finding the baseline the moment somebody changed the stop width.
    """
    panel = _tiny_panel()
    from backtest.engine import BacktestConfig
    from portfolio.exits import ExitConfig
    from portfolio.fees import FeeConfig
    cfg = BacktestConfig(rebalance="M", exits=ExitConfig(k_atr=2.75))
    out = exit_report(panel, 10_000_000, cfg, FeeConfig(), {}, None, None)

    shipped = out[out["variant"].str.contains("shipped")]
    assert len(shipped) == 1 and "2.75x" in shipped.iloc[0]["variant"]
    assert pd.isna(shipped.iloc[0]["verdict"])


# ==================================================== what the Method page reads
def test_the_verdict_carries_absolute_selling_days_not_only_the_gap():
    """
    "71 more selling days than holding" cannot be turned into a rupiah-a-year stamp
    bill. The Method page needs the count itself.
    """
    v = exit_verdict(pd.DataFrame([
        {"variant": "Hold to the rebalance (no stops)", "cagr_pct": 31.2,
         "max_drawdown_pct": -26.7, "sharpe": 1.16, "fees_paid_rp": 4_287_505,
         "sell_days": 167},
        {"variant": "Stop only, 2.5x ATR", "cagr_pct": 19.2,
         "max_drawdown_pct": -25.1, "sharpe": 0.68, "fees_paid_rp": 3_311_042,
         "sell_days": 167},
        {"variant": "Stop + ladder, 2.5x ATR (shipped)", "cagr_pct": 14.6,
         "max_drawdown_pct": -14.5, "sharpe": 0.59, "fees_paid_rp": 4_115_794,
         "sell_days": 238},
    ]))
    assert v["sell_days"] == 238
    assert v["extra_sell_days"] == 71     # the gap is still there, beside it


def test_no_exits_table_means_no_selling_days_rather_than_a_zero():
    """Zero would render as "you never sell", which is a different claim."""
    assert exit_verdict(pd.DataFrame())["sell_days"] is None
