"""
The build and CI configuration, asserted rather than assumed.

A dependency floor lets a rebuild months later resolve to different arithmetic
with nothing in this repository changing -- the ticket's numbers move and nothing
says why. The floors this project shipped with (`pandas>=2.1.0`, `numpy>=1.24.0`)
had already resolved to pandas 3.x and numpy 2.x by the time anyone looked.

And a CI file is only worth having if it runs the same two commands the developer
runs; one that drifts is worse than none, because it is green for the wrong reason.
"""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CI = ROOT / ".github" / "workflows" / "ci.yml"
REQ = ROOT / "requirements.txt"
DEV = ROOT / "requirements-dev.txt"


def _lines(path: Path):
    """Requirement lines, comments and blanks dropped."""
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith(("#", "-r"))]


# ------------------------------------------------------------------ the pins
def test_every_runtime_dependency_is_pinned_exactly():
    for line in _lines(REQ):
        assert "==" in line, f"not pinned: {line}"
        assert ">=" not in line and "<" not in line, f"range, not a pin: {line}"


def test_every_dev_dependency_is_pinned_exactly():
    for line in _lines(DEV):
        assert "==" in line, f"not pinned: {line}"


def test_the_dev_set_builds_on_the_runtime_set():
    """Two independent lists drift; the dev file must include the other."""
    assert "-r requirements.txt" in DEV.read_text(encoding="utf-8")


def test_the_pins_match_what_is_actually_installed():
    """
    A pin nobody runs against is a wish. This is the test that catches a bumped
    version left out of the file, or a file bumped ahead of the environment.
    """
    import importlib.metadata as md

    mismatched = []
    for line in _lines(REQ):
        name, _, want = line.partition("==")
        try:
            have = md.version(name)
        except md.PackageNotFoundError:
            mismatched.append(f"{name}: pinned {want}, not installed")
            continue
        if have != want:
            mismatched.append(f"{name}: pinned {want}, installed {have}")
    assert not mismatched, "; ".join(mismatched)


# --------------------------------------------------------------------- CI
def test_there_is_a_ci_workflow_and_it_parses():
    assert CI.exists(), "1123 tests and nothing runs them"
    yaml.safe_load(CI.read_text(encoding="utf-8"))


def test_ci_runs_the_same_two_commands_the_developer_runs():
    """
    Drift here is the failure mode: CI green on a narrower check than the one
    used locally teaches everyone that green means nothing.
    """
    text = CI.read_text(encoding="utf-8")
    assert "python -m pytest -q" in text
    assert "ruff check src/ tests/ --select F821,F811,E402" in text


def test_ci_runs_on_the_platform_this_actually_ships_on():
    """A green run on Linux is evidence about a program nobody uses."""
    assert "windows-latest" in CI.read_text(encoding="utf-8")


def test_ci_pins_python_too():
    cfg = yaml.safe_load(CI.read_text(encoding="utf-8"))
    steps = cfg["jobs"]["suite"]["steps"]
    setup = next(s for s in steps if str(s.get("uses", "")).startswith("actions/setup-python"))
    version = str(setup["with"]["python-version"])
    assert version.count(".") == 2, f"pin the patch too, got {version}"


@pytest.mark.parametrize("trigger", ["push", "pull_request"])
def test_ci_fires_without_being_asked(trigger):
    cfg = yaml.safe_load(CI.read_text(encoding="utf-8"))
    # PyYAML reads a bare `on:` key as the boolean True.
    on = cfg.get("on", cfg.get(True))
    assert trigger in on
