"""
The README may say what the method costs. It may not say what it earns.

The same line `evidence_note` already draws on the front page, applied to the
document. The app was taught to refuse to print an unproven edge figure; the README
went on asserting one -- *"it went from costing 0.6pp a year against an equal-weight
universe to adding 9.3pp"* -- as a hand-written literal with no config fingerprint
and nothing to check it against. Measured on the shipped configuration that
comparison came out negative, with the sign reversing between the halves of the
window.

The audience for this guard is not a stranger reading the repo. It is you, six
months from now, believing a number in your own documentation.

**Why `pp` and not `%`.** Every edge claim this project ever made was in percentage
points of CAGR; drawdowns, Sharpe and win rates are in `%`. Keying on `pp` separates
"what the ranking earns" from "what it costs and protects against" almost perfectly,
which is why this rule fires on the retired sentences and on nothing else in the
current text.
"""
import re
from pathlib import Path

import pytest

README = Path(__file__).resolve().parent.parent / "README.md"

# Claiming verbs. Substring matching on purpose: "beats", "beating", "adds", "added".
EDGE_VERBS = ("add", "cost", "beat", "outperform", "gain")

# A figure in percentage points -- "9.3pp", "+28.0 pp", "~157pp".
FIGURE = re.compile(r"[-+~]?\d+(?:\.\d+)?\s*pp\b")

# Retired by measurement. Never to return in any sentence, verb or no verb.
RETIRED = ("9.3pp", "0.6pp a year", "11.3pp")


def _prose(text: str):
    """(line number, line) for prose only -- fenced code blocks are examples, not claims."""
    out, fenced = [], False
    for n, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            out.append((n, line))
    return out


def claims(text: str):
    """Lines asserting an edge: a claiming verb and a percentage-point figure together."""
    return [(n, line) for n, line in _prose(text)
            if FIGURE.search(line) and any(v in line.lower() for v in EDGE_VERBS)]


# ------------------------------------------------------------------- the guard
def test_the_readme_claims_no_edge():
    found = claims(README.read_text(encoding="utf-8"))
    assert not found, "the README asserts an edge again:\n" + "\n".join(
        f"  line {n}: {line.strip()}" for n, line in found)


@pytest.mark.parametrize("phrase", RETIRED)
def test_the_retired_figures_stay_retired(phrase):
    """
    These three were measured wrong, under configurations that were not the shipped
    one. Pinned by value so they cannot come back in a rephrasing the verb rule misses.
    """
    assert phrase not in README.read_text(encoding="utf-8")


# ------------------------------------------- the guard can actually catch something
def test_the_guard_catches_the_sentence_that_prompted_it():
    """A rule that fires on nothing is not protecting anything. This is the real one."""
    sample = ("It went from **costing** 0.6pp a year against an equal-weight "
              "universe to **adding 9.3pp**.")
    assert claims(sample)


@pytest.mark.parametrize("line,flagged", [
    ("The ranking adds 9.3pp a year.", True),
    ("Removing the stops gains 15.9pp of CAGR.", True),
    ("Fees cost 236pp over the window.", True),          # a cost claim, still a claim
    ("| Gap over the index | +28.0pp | +28.1pp |", False),   # a figure, no claim
    ("a standard deviation of ~157pp", False),              # dispersion, not a claim
    ("| **Share of names beating the index** | **78%** | **73%** |", False),  # % not pp
    ("lifting Sharpe from 1.16 to 1.61", False),
])
def test_the_rule_separates_claims_from_measurements(line, flagged):
    assert bool(claims(line)) is flagged


def test_fenced_examples_are_not_read_as_claims():
    """A code block showing output is an illustration, not an assertion."""
    assert not claims("```\nranking adds 9.3pp\n```")
