"""
Nothing in this repository says what the author owns.

This is a public repository. `.gitignore` has always covered the obvious files --
`current_holdings.yaml`, `configs/user.yaml`, `data/` -- but ignoring a *filename*
protects nothing against a *value* pasted into a test, and that is exactly how this
leaked: three real positions were pinned in `tests/test_exits.py`, to the lot and to
the rupiah of average cost, because using the live position made the arithmetic
checkable against the terminal by eye.

**This file deliberately does not list the values it is protecting.** An earlier
draft did, and it was self-defeating twice over: the guard failed on itself the
moment it was tracked, and enumerating a private fill in a public file republishes
the very thing it claims to remove. Hashing them is no better -- a price with two
decimals lives in a search space small enough to exhaust in seconds.

So the rule is structural, and it is about *shape* rather than value:

    a real IDX ticker  +  a position construct  +  a price carrying cents
    =  somebody's actual fill

A fee-inclusive average cost is what a broker gives back -- a round order price
times 1.0019 -- and no invented fixture ever needs one. Market statistics are exempt
because an ATR is public: it is measurable by anyone from the same price history,
and it says nothing about who holds what. Invented tickers are exempt because that
is the fix this rule exists to encourage.

Scope, stated honestly: this looks at the working tree only. The values are already
in pushed history, and removing them there needs a rewrite and a force-push over
published commits -- a bigger risk than the disclosure, which is of positions that
are no longer current. This test stops the next one.
"""
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent

# Files holding capital, positions, trade history, or a private event calendar.
SENSITIVE = (
    "current_holdings.yaml",
    "configs/user.yaml",
    "configs/events.yaml",
    "data/journal.csv",
    "data/cash.csv",
    "data/dividends.csv",
    "data/marks.csv",
    ".env",
)

# Somewhere a position is being constructed.
POSITION = re.compile(r"plan_for\(|Holding\(|avg_price|avg_cost|entry=")

# A price carrying cents. A round fixture price does not; a real fill does.
CENTS = re.compile(r"\d+\.\d{2,}")

# Measured market statistics, stripped before the cents test. An ATR is public --
# anyone with the same price history computes the same number -- so its precision
# is not a disclosure. An entry price is not public.
MARKET_STAT = re.compile(r"\b(?:atr_rp|atr|risk|high|price_now)\s*=\s*[\d.]+")


def _git(*args) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                         text=True, timeout=30)
    if out.returncode != 0:
        pytest.skip("not a git repository, or git is unavailable")
    return out.stdout


@pytest.fixture(scope="module")
def tracked():
    return [ln for ln in _git("ls-files").splitlines() if ln.strip()]


@pytest.fixture(scope="module")
def universe():
    """The real IDX names, read from the config that already publishes them."""
    found = set()

    def walk(node):
        if isinstance(node, str):
            if re.fullmatch(r"[A-Z]{2,5}\.JK", node):
                found.add(node)
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(k)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(yaml.safe_load((ROOT / "configs" / "default.yaml").read_text(encoding="utf-8")))
    assert len(found) > 50, "the universe did not parse; the guard would pass vacuously"
    return found


# ------------------------------------------------------------------ the paths
@pytest.mark.parametrize("path", SENSITIVE)
def test_the_sensitive_paths_are_ignored(path):
    """`check-ignore` asks git itself rather than re-parsing .gitignore by hand."""
    out = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, timeout=30)
    assert out.returncode == 0, f"{path} is not ignored"


@pytest.mark.parametrize("path", SENSITIVE)
def test_the_sensitive_paths_are_untracked(path, tracked):
    """
    Ignoring a file does nothing once it is tracked -- git goes on publishing a file
    it already follows, and .gitignore never applies to it again.
    """
    assert path not in tracked, f"{path} is committed to a public repository"


def test_no_output_or_cache_is_tracked(tracked):
    """`data/output/brief.html` renders capital, holdings and every open position."""
    leaked = [p for p in tracked
              if p.startswith(("data/", "logs/")) or p.endswith(".replaced")]
    assert not leaked, f"tracked personal output: {leaked}"


def test_the_example_files_are_the_only_holdings_files_tracked(tracked):
    """The `.example` twin is a template and safe; its real counterpart is not."""
    assert [p for p in tracked if "current_holdings" in p] \
        == ["current_holdings.example.yaml"]


# ------------------------------------------------------------------ the shape
def _fills(text: str, universe) -> list:
    """Lines pairing a real ticker with a position priced to the cent."""
    out = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not (POSITION.search(line) and any(t in line for t in universe)):
            continue
        if CENTS.search(MARKET_STAT.sub("", line)):
            out.append((n, line.strip()))
    return out


def test_no_tracked_file_pins_a_real_position(tracked, universe):
    """
    The rule the gitignore could not enforce -- and on its first run it found a
    third fill nobody had remembered was there, on a name that had never been
    mentioned as a holding. Invented tickers and round prices test the same
    arithmetic exactly as well.
    """
    hits = []
    for rel in tracked:
        if not rel.endswith(".py"):
            continue
        # This file states the rule, so it has to contain lines that break it --
        # the probes below. They are invented, and they are the only exemption.
        if Path(rel).name == Path(__file__).name:
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        hits += [f"{rel}:{n}: {line}" for n, line in _fills(text, universe)]
    assert not hits, "a real holding is pinned in a public repository:\n" + "\n".join(hits)


# ---------------------------------------------- the guard can catch something
# Invented fills, on a real ticker, shaped exactly like the ones this rule caught.
# The values are made up: a probe carrying a genuine fill would be the same mistake
# this file exists to prevent, and the rule cannot tell the difference anyway --
# which is the point of testing shape rather than value.
@pytest.mark.parametrize("line,flagged", [
    ('holdings = [Holding("BBRI.JK", lots=41, avg_price=123.45)]', True),
    ('plan_for("BBRI.JK", 10, 2345.6789, closes, CFG, FEES, atr_rp=11.11,', True),
    ('plan_for("BBRI.JK", 36, 366.0, closes, CFG, FEES, atr_rp=24.51,', False),
    ('holdings = [Holding("WINNER.JK", lots=40, avg_price=1000.00)]', False),
    ('m = _corr({("TINS.JK", "ADRO.JK"): 0.85}, tickers)', False),
])
def test_the_rule_separates_a_fill_from_a_measurement(line, flagged, universe):
    """
    A rule that fires on nothing protects nothing. The third case is the one that
    keeps it usable: a published ATR beside a round price is market data, not a
    position, and flagging it would train everyone to ignore this test.
    """
    assert bool(_fills(line, universe)) is flagged
