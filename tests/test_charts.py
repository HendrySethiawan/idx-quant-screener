"""
Tests for the inline SVG chart primitives.

These charts exist to replace a 896KB matplotlib PNG that no page linked to and
whose colours were baked white. The properties worth guarding are therefore not
"does it look nice" but: it never divides by zero on degenerate data, it renders a
gap as a gap rather than interpolating across it, it escapes whatever text it is
handed, and it references CSS variables so it survives a theme switch.
"""
import math
import re

import pytest

from report import charts


def _svg_count(s):
    return s.count("<svg"), s.count("</svg>")


# ------------------------------------------------------------------ well-formed
@pytest.mark.parametrize("call", [
    lambda: charts.line_chart([("a", [1.0, 2.0, 3.0])]),
    lambda: charts.bar_chart(["Jan", "Feb"], [1.5, -2.0]),
    lambda: charts.diverging_bars([("P/E", 0.8), ("Vol", -1.2)]),
    lambda: charts.heatmap([[1.0, 0.4], [0.4, 1.0]], ["a", "b"]),
])
def test_every_chart_is_one_balanced_svg(call):
    out = call()
    assert _svg_count(out) == (1, 1)
    assert 'viewBox="0 0' in out
    assert 'role="img"' in out


@pytest.mark.parametrize("call", [
    lambda: charts.line_chart([("a", [1.0, 2.0, 3.0])]),
    lambda: charts.bar_chart(["Jan"], [1.5]),
    lambda: charts.diverging_bars([("P/E", 0.8)]),
    lambda: charts.heatmap([[1.0]], ["a"]),
])
def test_charts_theme_themselves(call):
    """
    Colours must be CSS variables, not literals. This is the whole reason these
    replaced the matplotlib PNG, which is saved with facecolor="white".
    """
    out = call()
    assert "var(--" in out
    assert not re.search(r'(fill|stroke)="#[0-9a-fA-F]{3,6}"', out), \
        "a hard-coded colour will not follow the reader's light/dark theme"


# --------------------------------------------------------------- degenerate data
@pytest.mark.parametrize("call", [
    lambda: charts.line_chart([]),
    lambda: charts.line_chart([("a", [])]),
    lambda: charts.line_chart([("a", [1.0])]),           # a single point is not a line
    lambda: charts.line_chart([("a", [None, float("nan")])]),
    lambda: charts.bar_chart([], []),
    lambda: charts.bar_chart(["Jan"], [None]),
    lambda: charts.diverging_bars([]),
    lambda: charts.diverging_bars([("x", float("nan"))]),
    lambda: charts.heatmap([], []),
])
def test_no_data_degrades_to_a_message_not_a_crash(call):
    out = call()
    assert "<svg" not in out
    assert "Not enough data" in out


def test_a_flat_series_does_not_divide_by_zero():
    """lo == hi would be a ZeroDivisionError in every scale call."""
    out = charts.line_chart([("flat", [7000.0] * 30)])
    assert "<svg" in out
    assert "nan" not in out.lower() and "inf" not in out.lower()


def test_all_zero_bars_still_render():
    out = charts.bar_chart(["a", "b"], [0.0, 0.0])
    assert "<svg" in out
    assert "nan" not in out.lower()


# ------------------------------------------------------------------------- gaps
def test_a_gap_in_the_data_breaks_the_line():
    """
    A missing observation must not be interpolated across. Two "M" commands means
    the path lifted the pen and restarted.
    """
    out = charts.line_chart([("a", [1.0, 2.0, None, 4.0, 5.0])])
    path = re.search(r'<path d="([^"]+)"', out).group(1)
    assert path.count("M") == 2, f"expected a broken path, got {path}"


def test_an_unbroken_line_is_a_single_path():
    out = charts.line_chart([("a", [1.0, 2.0, 3.0, 4.0])])
    path = re.search(r'<path d="([^"]+)"', out).group(1)
    assert path.count("M") == 1


# ------------------------------------------------------------------- escaping
@pytest.mark.parametrize("call", [
    lambda t: charts.line_chart([(t, [1.0, 2.0])]),
    lambda t: charts.bar_chart([t], [1.0]),
    lambda t: charts.diverging_bars([(t, 1.0)]),
    lambda t: charts.heatmap([[1.0]], [t]),
])
def test_labels_are_escaped(call):
    out = call("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


# ---------------------------------------------------------------- semantics
def test_diverging_bars_sort_by_magnitude_not_by_sign():
    """The reader wants the biggest driver first, whichever way it pushed."""
    out = charts.diverging_bars([("small", 0.1), ("big-negative", -2.0), ("mid", 0.9)])
    order = [m for m in re.findall(r">(small|big-negative|mid)<", out)]
    assert order == ["big-negative", "mid", "small"]


def test_diverging_bars_colour_by_direction():
    out = charts.diverging_bars([("up", 1.0), ("down", -1.0)])
    assert "var(--good)" in out and "var(--bad)" in out


def test_bar_chart_muting_keeps_the_bar_visible():
    """
    A thin-sample month is greyed, never dropped: an absent bar reads as
    "no effect", which is a stronger claim than "we do not know".
    """
    out = charts.bar_chart(["Jan", "Feb"], [1.0, 2.0], muted=[True, False])
    assert out.count("<rect") >= 2
    assert "var(--muted)" in out


def test_bar_chart_annotations_are_rendered():
    out = charts.bar_chart(["Jan"], [1.0], annotations=["n=37"])
    assert "n=37" in out


def test_heatmap_prints_the_number_in_every_cell():
    """A heatmap you have to eyeball against a colour bar is not an answer."""
    out = charts.heatmap([[1.0, -0.55], [-0.55, 1.0]], ["a", "b"])
    assert "-0.55" in out
    assert out.count("<text") >= 4


def test_heatmap_tolerates_a_ragged_matrix():
    out = charts.heatmap([[1.0, 0.2], [0.2]], ["a", "b"])
    assert "<svg" in out


def test_coordinates_stay_finite():
    """Any NaN reaching an attribute silently blanks the whole chart in a browser."""
    out = charts.line_chart(
        [("a", [1.0, float("inf"), 3.0, float("nan"), 5.0])],
        x_labels=["x"] * 5,
    )
    # The leading \s matters: without it "viewBox" ends in an x and matches.
    for value in re.findall(r'\s(?:x|y|x1|y1|x2|y2|width|height)="([^"]+)"', out):
        assert math.isfinite(float(value)), f"non-finite coordinate {value!r}"
