# src/backtest/report.py
"""
The three questions the backtest can honestly answer, plus a robustness grid.

Every report carries the same standing caveat, because a number without it invites
exactly the misreading this phase was built to prevent: only a third of the live
score is testable, the universe is today's survivors, and this is one market
regime.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from backtest.engine import (BacktestConfig, buy_and_hold, equal_weight_universe,
                             rebalance_dates, run_backtest)
from portfolio.fees import FeeConfig
from report.brief import _e, _kpi, _table, rp
from report.terminal import DOC_CSS, THEME_CSS

CAVEAT = (
    "This tests the price factors only - momentum and realised volatility, which are "
    "3.0 of the 9.0 total factor weight. The six fundamental factors (P/E, P/B, "
    "dividend yield, ROE, gross margin, debt/equity) come from Yahoo as a current "
    "snapshot, so using them historically would be look-ahead. The universe is also "
    "today's surviving tickers, and this is a single market regime. A good result "
    "here means the price component was not obviously broken on one flattered "
    "window - it does not mean the tool works."
)


@dataclass
class Comparison:
    label: str
    equity: pd.Series
    metrics: Dict[str, Optional[float]] = field(default_factory=dict)
    note: str = ""


def _pct(v, digits: int = 1) -> str:
    return "-" if v is None else f"{v:+.{digits}f}%"


def survivorship_check(panel, benchmark, dates, capital) -> Dict[str, Optional[float]]:
    """
    How much of the backtest's return came from the universe rather than the strategy.

    This is the largest single effect in the whole simulation and it is an artifact:
    the 49 tickers were chosen in 2026, knowing which companies still exist. Holding
    all of them equally, with no ranking, no sizing and no timing, is compared here
    against the index over the same window. On the current universe that gap is about
    +27% per year -- an order of magnitude larger than anything the ranking adds.

    Reported on every run so the headline CAGR is never read without it.
    """
    out = {"universe_cagr": None, "index_cagr": None, "gap_cagr": None,
           "n_beating_index": None, "n_names": None, "median_name_return": None}
    if panel is None or panel.empty or benchmark is None or benchmark.empty:
        return out

    bench = benchmark.dropna()
    years = (bench.index[-1] - bench.index[0]).days / 365.25
    if years <= 0:
        return out

    ew = equal_weight_universe(panel, dates, capital)
    if ew.empty:
        return out

    idx_cagr = (float(bench.iloc[-1] / bench.iloc[0]) ** (1 / years) - 1) * 100
    uni_cagr = (float(ew.iloc[-1] / ew.iloc[0]) ** (1 / years) - 1) * 100

    first = panel.apply(lambda c: c.dropna().iloc[0] if c.notna().any() else float("nan"))
    last = panel.apply(lambda c: c.dropna().iloc[-1] if c.notna().any() else float("nan"))
    rets = (last / first - 1).dropna()
    bench_total = float(bench.iloc[-1] / bench.iloc[0] - 1)

    out.update({
        "universe_cagr": round(uni_cagr, 1),
        "index_cagr": round(idx_cagr, 1),
        "gap_cagr": round(uni_cagr - idx_cagr, 1),
        "n_beating_index": int((rets > bench_total).sum()),
        "n_names": int(len(rets)),
        "median_name_return": round(float(rets.median()) * 100, 1),
    })
    return out


def survivorship_text(s: Dict[str, Optional[float]]) -> str:
    if not s or s.get("gap_cagr") is None:
        return ""
    return (
        f"Universe selection is the biggest effect here, and it is an artifact. Simply "
        f"holding all {s['n_names']} of these tickers equally - no ranking, no sizing, no "
        f"timing - returned {s['universe_cagr']:+.1f}% a year while the index returned "
        f"{s['index_cagr']:+.1f}%. That is a {s['gap_cagr']:+.1f} percentage point per year "
        f"gap from the ticker list alone. {s['n_beating_index']} of {s['n_names']} names beat "
        f"the index and the median one returned {s['median_name_return']:+.0f}%, which is not "
        f"what a list drawn in 2021 would have looked like - it is what a list drawn in 2026 "
        f"looks like. Any edge the ranking adds sits on top of that, and is far smaller than it."
    )


# ------------------------------------------------- 1. do the factors add value?
def factor_report(panel, capital, cfg, fee_cfg, sectors, benchmark, fx,
                  trend_ma=200, deploy_ladder=(0.30, 0.60, 1.00)) -> List[Comparison]:
    """
    Strategy versus the index versus an equal-weight universe.

    The equal-weight line is the one that matters: it separates "the ranking added
    something" from "IDX stocks went up". Both benchmarks are frictionless, so the
    honest comparison is against the strategy's GROSS curve -- the net curve is
    shown alongside as what would actually have reached the account.
    """
    from backtest.engine import summarize

    dates = rebalance_dates(panel, cfg.rebalance)
    ppy = cfg.periods_per_year
    # The same rate on both sides. Subtracting it from the strategy alone would
    # hand the benchmark five points a year it never earned.
    rf = getattr(cfg, "risk_free_pct", 0.0)

    gross_cfg = BacktestConfig(**{**cfg.__dict__, "charge_fees": False, "whole_lots": False})
    gross = run_backtest(panel, capital, gross_cfg, fee_cfg, sectors, benchmark, fx,
                         trend_ma, deploy_ladder)
    net = run_backtest(panel, capital, cfg, fee_cfg, sectors, benchmark, fx,
                       trend_ma, deploy_ladder)

    out = [
        Comparison("Strategy (gross, frictionless)", gross.equity, gross.metrics(),
                   "comparable to the benchmarks below"),
        Comparison("Strategy (net: real lots + fees)", net.equity, net.metrics(),
                   "what would actually have reached the account"),
    ]

    ew = equal_weight_universe(panel, dates, capital)
    if not ew.empty:
        out.append(Comparison(
            "Equal-weight universe", ew,
            summarize(ew, ew.pct_change().dropna(), pd.Series(dtype=float), ppy, rf),
            "every listed name, no ranking - did the ranking beat this?",
        ))

    if benchmark is not None and len(benchmark.dropna()):
        idx = buy_and_hold(benchmark, dates, capital)
        if not idx.empty:
            out.append(Comparison(
                "IHSG (buy and hold)", idx,
                summarize(idx, idx.pct_change().dropna(), pd.Series(dtype=float), ppy, rf),
                "the do-nothing alternative",
            ))
    return out


# ------------------------------------------------ 2. what does being small cost?
def cost_report(panel, capital, cfg, fee_cfg, sectors, benchmark, fx,
                trend_ma=200, deploy_ladder=(0.30, 0.60, 1.00)) -> pd.DataFrame:
    """
    Separate the true costs from the noise, because they behave differently.

    **Fees and stamp are a genuine drag.** Hold everything else fixed and charging
    them always lowers the result. That difference is a cost.

    **Whole-lot rounding is not.** Measured on the real universe by varying the
    start month, the rounding effect was positive in 7 of 14 windows with a
    standard deviation of about 157 percentage points. At Rp10 juta, rounding is a
    large source of *path noise*, not a predictable tax -- reporting it as "the
    cost of lots" would be inventing a sign that the data does not support.

    So rounding is reported two ways: the deterministic part (`undeployed_pct`, the
    share of budget that could not be put to work, always >= 0), and the path effect
    labelled as noise.

    On the stamp line: in a backtest every sell for a rebalance lands on one date,
    so stamp is already optimally batched. The monthly-versus-weekly comparison is
    what shows the cost of trading more often.
    """
    def run(**over):
        c = BacktestConfig(**{**cfg.__dict__, **over})
        return run_backtest(panel, capital, c, fee_cfg, sectors, benchmark, fx,
                            trend_ma, deploy_ladder)

    exact = run(charge_fees=False, whole_lots=False, min_position_rp=0)
    lots = run(charge_fees=False, whole_lots=True, min_position_rp=0)
    floored = run(charge_fees=False, whole_lots=True)
    net = run(charge_fees=True, whole_lots=True)

    def total(r):
        return None if r.equity.empty else round(float(r.equity.iloc[-1] / r.equity.iloc[0] - 1) * 100, 2)

    def delta(a, b):
        ta, tb = total(a), total(b)
        return None if ta is None or tb is None else round(tb - ta, 2)

    rows = [
        {"item": "Exact weights, no fees (reference)", "kind": "reference",
         "total_return_pct": total(exact), "effect_pp": None, "detail": ""},
        {"item": "Whole-lot rounding", "kind": "noise",
         "total_return_pct": total(lots), "effect_pp": delta(exact, lots),
         "detail": f"{lots.avg_undeployed_pct:.1f}% of budget left in cash on average; "
                   "the return effect is path luck, not a cost"},
        {"item": "Minimum position size", "kind": "noise",
         "total_return_pct": total(floored), "effect_pp": delta(lots, floored),
         "detail": "fewer, larger positions - protects against fee-dominated trades"},
        {"item": "Broker fees + stamp duty", "kind": "cost",
         "total_return_pct": total(net), "effect_pp": delta(floored, net),
         "detail": f"{rp(net.fees_paid)} paid, of which {rp(net.stamp_paid)} stamp - "
                   "always a drag"},
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------- 3. does the ladder help?
def regime_report(panel, capital, cfg, fee_cfg, sectors, benchmark, fx,
                  trend_ma=200, deploy_ladder=(0.30, 0.60, 1.00)) -> pd.DataFrame:
    """
    Always-invested versus the deploy ladder, on return AND drawdown.

    A ladder that costs return but halves drawdown is a legitimate trade. The table
    shows both columns rather than picking a winner for the reader.
    """
    rows = []
    for label, use_regime in (("Always 100% deployed", False), ("Regime ladder 30/60/100", True)):
        c = BacktestConfig(**{**cfg.__dict__, "use_regime": use_regime})
        r = run_backtest(panel, capital, c, fee_cfg, sectors, benchmark, fx,
                         trend_ma, deploy_ladder)
        if r.equity.empty:
            continue
        m = r.metrics()
        rows.append({
            "setting": label,
            "cagr_pct": m.get("cagr"),
            "max_drawdown_pct": m.get("max_drawdown"),
            "sharpe": m.get("sharpe"),
            "final_value_rp": round(float(r.equity.iloc[-1])),
        })
    return pd.DataFrame(rows)


# ------------------------------------------------------------- 4. robustness
def robustness_report(panel, capital, cfg, fee_cfg, sectors, benchmark, fx,
                      trend_ma=200, deploy_ladder=(0.30, 0.60, 1.00)) -> pd.DataFrame:
    """
    Vary the settings and the window. An edge that survives only one configuration
    is not an edge.
    """
    rows = []

    def add(variant, over, start=None, end=None):
        c = BacktestConfig(**{**cfg.__dict__, **over, "start": start, "end": end})
        r = run_backtest(panel, capital, c, fee_cfg, sectors, benchmark, fx,
                         trend_ma, deploy_ladder)
        if r.equity.empty:
            return
        m = r.metrics()
        rows.append({
            "variant": variant,
            "cagr_pct": m.get("cagr"),
            "max_drawdown_pct": m.get("max_drawdown"),
            "sharpe": m.get("sharpe"),
            "periods": m.get("periods"),
        })

    add("baseline", {})
    for scale in (0.5, 1.5):
        add(f"momentum weights x{scale}", {"weight_scale": scale})
    for n in (3, 4, 5, 6):
        add(f"max {n} positions", {"max_positions": n, "min_positions": min(3, n)})

    dates = rebalance_dates(panel, cfg.rebalance)
    if len(dates) > 8:
        mid = dates[len(dates) // 2]
        add("first half only", {}, end=mid)
        add("second half only", {}, start=mid)

    return pd.DataFrame(rows)


VERDICT_FILE = "backtest_verdict.json"


def verdict_payload(factors: List[Comparison], robustness: pd.DataFrame,
                    survivorship: Dict[str, Optional[float]], cadence: str,
                    costs: Optional[pd.DataFrame] = None) -> dict:
    """
    What the backtest concluded, small enough for the brief to read on every run.

    The conclusions used to live only in `backtest.html`, which exists only if you
    remember to pass `--backtest`. So the page that says "BUY SRTG 39 lot" carried
    none of the evidence about what that ranking is worth, while a file three
    directories away said it worked in one half of the window. This closes that
    gap without making the brief re-run a five-year simulation.

    JSON rather than a pickle: it is small, it is worth being able to read by eye,
    and it must survive a version of this code that no longer exists.
    """
    def metrics_of(label_starts: str) -> Dict[str, Optional[float]]:
        for c in factors or []:
            if c.label.startswith(label_starts):
                return dict(c.metrics or {})
        return {}

    gross = metrics_of("Strategy (gross")
    equal = metrics_of("Equal-weight")
    index = metrics_of("IHSG")

    def gap(key: str) -> Optional[float]:
        a, b = gross.get(key), equal.get(key)
        return None if a is None or b is None else round(a - b, 2)

    return {
        "cadence": cadence,
        "generated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"),
        # Gross against frictionless, which is the only fair comparison -- see
        # `equal_weight_universe`, which says so itself.
        "gross": gross,
        "equal_weight": equal,
        "index": index,
        "cagr_gap_vs_equal_pp": gap("cagr"),
        "sharpe_gap_vs_equal": gap("sharpe"),
        "robustness": robustness_verdict(robustness),
        "survivorship": dict(survivorship or {}),
        # What trading actually cost over the window, at the account size the
        # backtest ran on. At Rp10 juta this was the largest controllable effect in
        # the whole simulation and the ticket never mentioned it.
        "costs": _cost_summary(costs),
    }


def _cost_summary(costs: Optional[pd.DataFrame]) -> dict:
    out: Dict[str, Optional[float]] = {"fee_effect_pp": None, "gross_return_pct": None,
                                       "fee_share_of_gross_pct": None, "detail": ""}
    if costs is None or costs.empty or "kind" not in costs:
        return out

    fee_rows = costs[costs["kind"] == "cost"]
    if fee_rows.empty:
        return out

    row = fee_rows.iloc[-1]
    effect = float(row.get("effect_pp") or 0.0)
    # The path the fees were charged against: the step immediately before them.
    before = costs.iloc[max(0, len(costs) - len(fee_rows) - 1)]
    gross = float(before.get("total_return_pct") or 0.0)

    out["fee_effect_pp"] = round(effect, 2)
    out["gross_return_pct"] = round(gross, 2)
    if gross > 0:
        out["fee_share_of_gross_pct"] = round(abs(effect) / gross * 100, 1)
    out["detail"] = str(row.get("detail") or "")
    return out


def write_verdict(payload: dict, output_dir) -> Path:
    import json

    path = Path(output_dir) / VERDICT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_verdict(output_dir) -> Optional[dict]:
    """The stored conclusion, or None if this machine has never run a backtest."""
    import json

    path = Path(output_dir) / VERDICT_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def robustness_verdict(table: pd.DataFrame) -> str:
    """Say plainly whether the result survived being stressed."""
    if table.empty or "cagr_pct" not in table:
        return "Not enough data to judge robustness."

    cagrs = table["cagr_pct"].dropna()
    if cagrs.empty:
        return "Not enough data to judge robustness."

    positive = int((cagrs > 0).sum())
    total = len(cagrs)
    halves = table[table["variant"].str.contains("half", na=False)]["cagr_pct"].dropna()

    bits = [f"{positive} of {total} variants produced a positive CAGR."]
    if len(halves) == 2:
        if (halves > 0).all():
            bits.append("It held up in both halves of the window.")
        elif (halves <= 0).all():
            bits.append("It was negative in both halves.")
        else:
            bits.append("It worked in only ONE half of the window - that is a warning, "
                        "not a result.")
    if positive == total:
        bits.append("Surviving every variant is encouraging, though still one market.")
    elif positive <= total / 2:
        bits.append("Failing half the variants means any edge here is not dependable.")
    return " ".join(bits)


# ------------------------------------------------------------------- rendering
def console_block(factors, costs, regimes, robustness, verdict, cadence, avg_names,
                  survivorship=None) -> str:
    L = ["", "=" * 68, f"BACKTEST - {cadence} rebalance", "=" * 68, ""]
    for line in _wrap(CAVEAT, 66):
        L.append("  " + line)
    L.append("")
    L.append(f"  Average names available per rebalance: {avg_names:.0f}")
    L.append("")

    text = survivorship_text(survivorship or {})
    if text:
        L.append("  !! READ THIS BEFORE THE NUMBERS BELOW !!")
        for line in _wrap(text, 66):
            L.append("  " + line)
        L.append("")

    L.append("1. DID THE PRICE FACTORS BEAT THE ALTERNATIVES?")
    L.append(f"   {'':38s} {'CAGR':>8s} {'maxDD':>8s} {'Sharpe':>7s}")
    for c in factors:
        m = c.metrics
        L.append(f"   {c.label:<38s} {_pct(m.get('cagr')):>8s} "
                 f"{_pct(m.get('max_drawdown')):>8s} {str(m.get('sharpe') or '-'):>7s}")
    L.append("")

    L.append("2. WHAT DOES BEING SMALL COST?")
    L.append("   (COST = always a drag.  NOISE = sign depends on luck, not a tax)")
    for _, r in costs.iterrows():
        eff = f"{r['effect_pp']:+8.2f} pp" if pd.notna(r["effect_pp"]) else " " * 11
        tag = r["kind"].upper() if r["kind"] != "reference" else ""
        ret = f"{r['total_return_pct']:+.2f}%" if pd.notna(r["total_return_pct"]) else "-"
        L.append(f"   {r['item']:<34s} {ret:>10s} {eff}  {tag}")
        if r["detail"]:
            for line in _wrap(str(r["detail"]), 58):
                L.append(f"        {line}")
    L.append("")

    L.append("3. DOES THE RISK-OFF LADDER HELP?")
    for _, r in regimes.iterrows():
        L.append(f"   {r['setting']:<38s} {_pct(r['cagr_pct']):>8s} "
                 f"{_pct(r['max_drawdown_pct']):>8s}")
    L.append("")

    L.append("4. DID IT SURVIVE BEING STRESSED?")
    for _, r in robustness.iterrows():
        L.append(f"   {r['variant']:<38s} {_pct(r['cagr_pct']):>8s} "
                 f"{_pct(r['max_drawdown_pct']):>8s}")
    L.append("")
    for line in _wrap(verdict, 66):
        L.append("  " + line)
    L.append("=" * 68)
    return "\n".join(L)


def _wrap(text: str, width: int) -> List[str]:
    import textwrap
    return textwrap.wrap(text, width)


def render_html(sections: Dict[str, dict], survivorship: Optional[dict] = None) -> str:
    """One page per run, reusing the brief's CSS and table helpers."""
    text = survivorship_text(survivorship or {})
    surv_html = (f'<div class="callout" style="border-left-color:var(--bad)">'
                 f'<strong>And read this too.</strong> {_e(text)}</div>') if text else ""
    body = ""
    for cadence, s in sections.items():
        rows = [[_e(c.label), _pct(c.metrics.get("cagr")), _pct(c.metrics.get("max_drawdown")),
                 str(c.metrics.get("sharpe") or "-"), f'<span class="note">{_e(c.note)}</span>']
                for c in s["factors"]]
        factor_tbl = _table(["Strategy / benchmark", "CAGR", "Max drawdown", "Sharpe", "Note"],
                            rows, num_cols={1, 2, 3})

        cost_rows = []
        for _, r in s["costs"].iterrows():
            kind = r["kind"]
            pill = {"cost": "bad", "noise": "warn"}.get(kind, "")
            label = {"cost": "cost", "noise": "noise", "reference": "-"}[kind]
            cost_rows.append([
                _e(r["item"]),
                "-" if pd.isna(r["total_return_pct"]) else f'{r["total_return_pct"]:+.2f}%',
                "" if pd.isna(r["effect_pp"]) else f'{r["effect_pp"]:+.2f} pp',
                f'<span class="pill {pill}">{label}</span>',
                f'<span class="note">{_e(r["detail"])}</span>',
            ])
        cost_tbl = _table(["Step", "Total return", "Effect", "Type", "What it means"],
                          cost_rows, num_cols={1, 2})

        reg_rows = [[_e(r["setting"]), _pct(r["cagr_pct"]), _pct(r["max_drawdown_pct"]),
                     str(r["sharpe"] or "-")] for _, r in s["regimes"].iterrows()]
        reg_tbl = _table(["Setting", "CAGR", "Max drawdown", "Sharpe"], reg_rows,
                         num_cols={1, 2, 3})

        rob_rows = [[_e(r["variant"]), _pct(r["cagr_pct"]), _pct(r["max_drawdown_pct"]),
                     str(r["sharpe"] or "-")] for _, r in s["robustness"].iterrows()]
        rob_tbl = _table(["Variant", "CAGR", "Max drawdown", "Sharpe"], rob_rows,
                         num_cols={1, 2, 3})

        body += f"""
<h2>{_e(cadence)} rebalance</h2>
<div class="kpis">{_kpi("Rebalances", str(s["n_rebalances"]))}
{_kpi("Avg names available", f'{s["avg_names"]:.0f}')}
{_kpi("Fees paid", rp(s["fees_paid"]))}</div>
<div class="card"><h3>1. Did the price factors beat the alternatives?</h3>{factor_tbl}</div>
<div class="card"><h3>2. What does being small cost?</h3>{cost_tbl}</div>
<div class="card"><h3>3. Does the risk-off ladder help?</h3>{reg_tbl}</div>
<div class="card"><h3>4. Did it survive being stressed?</h3>{rob_tbl}
<div class="callout">{_e(s["verdict"])}</div></div>"""

    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IDX Backtest</title>
<style>{THEME_CSS}{DOC_CSS}
</style>
<div class="wrap">
<header><h1>Backtest</h1>
<div class="sub">Price factors only, under real trading frictions</div></header>
<div class="callout"><strong>Read this first.</strong> {_e(CAVEAT)}</div>
{surv_html}
{body}
<footer><p>A personal research tool, not investment advice. Past behaviour on a
survivorship-biased universe is not a forecast.</p></footer>
</div>
"""


def write_html(text: str, output_dir: Path, filename: str = "backtest.html") -> Path:
    out = Path(output_dir) / filename
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out
