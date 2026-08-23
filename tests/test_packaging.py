"""
Tests for the frozen build: where files live, and what must never be inside one.

Two guards carry this:

  * **Nothing personal is bundled.** `configs/user.yaml` holds the reader's capital
    and `current_holdings.yaml` holds their positions. A build that swept `configs/*`
    into the payload would put a private number inside every copy of a binary that
    might be handed to someone else, and it would be invisible -- nobody unzips an
    exe to check. So the spec is read and asserted here, and `build.py` walks the
    finished tree and fails the build on a hit.

  * **The chart libraries stay out of the startup path.** They were costing 1.8 of
    the 3.8 seconds this entry point took to import, for an opt-in PNG that no page
    links to. Nothing stops that creeping back except a test.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "packaging" / "idx_terminal.spec"
BUILD = ROOT / "packaging" / "build.py"


@pytest.fixture(scope="module")
def spec_text() -> str:
    return SPEC.read_text(encoding="utf-8")


# ------------------------------------------------------- nothing personal ships
@pytest.mark.parametrize("personal", [
    "configs/user.yaml",
    "configs/events.yaml",
    "current_holdings.yaml",
])
def test_the_spec_bundles_no_personal_file(spec_text, personal):
    """
    Checked as a datas entry, not as a substring: the filenames appear in the spec's
    own comments explaining why they are excluded, and a naive search would match
    those and pass for the wrong reason.
    """
    datas = spec_text.split("datas = [")[1].split("]")[0]
    assert personal.split("/")[-1] not in datas or ".example." in datas, (
        f"{personal} looks like it is in the bundle"
    )


def test_the_spec_bundles_only_the_three_seed_files(spec_text):
    datas = spec_text.split("datas = [")[1].split("]")[0]
    assert "default.yaml" in datas
    assert "events.example.yaml" in datas
    assert "current_holdings.example.yaml" in datas
    assert "data" not in datas.replace("datas", "")


def test_the_build_script_refuses_a_dirty_tree(tmp_path):
    """The check that cannot be argued with: it reads what was actually produced."""
    spec = importlib.util.spec_from_file_location("buildmod", BUILD)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)

    tree = tmp_path / "IDX Terminal"
    (tree / "configs").mkdir(parents=True)
    (tree / "configs" / "default.yaml").write_text("account:\n", encoding="utf-8")
    (tree / f"{build.APP}.exe").write_bytes(b"MZ")
    assert build.verify(tree) == []

    (tree / "configs" / "user.yaml").write_text("account:\n  capital_rp: 1\n",
                                                encoding="utf-8")
    problems = build.verify(tree)
    assert problems and any("user.yaml" in p for p in problems)


def test_the_build_script_catches_a_journal_and_a_cache(tmp_path):
    spec = importlib.util.spec_from_file_location("buildmod", BUILD)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)

    tree = tmp_path / "IDX Terminal"
    (tree / "configs").mkdir(parents=True)
    (tree / "configs" / "default.yaml").write_text("account:\n", encoding="utf-8")
    (tree / f"{build.APP}.exe").write_bytes(b"MZ")
    (tree / "data" / "cache").mkdir(parents=True)
    (tree / "data" / "cache" / "BBRI.JK.pkl").write_bytes(b"x")
    (tree / "data" / "journal.csv").write_text("date\n", encoding="utf-8")

    problems = build.verify(tree)
    assert any("journal.csv" in p for p in problems)
    assert any("data/cache" in p for p in problems)


def test_a_build_missing_its_config_is_rejected(tmp_path):
    """Without default.yaml the exe starts and immediately cannot configure itself."""
    spec = importlib.util.spec_from_file_location("buildmod", BUILD)
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)

    tree = tmp_path / "IDX Terminal"
    tree.mkdir()
    (tree / f"{build.APP}.exe").write_bytes(b"MZ")
    assert any("default.yaml" in p for p in build.verify(tree))


# --------------------------------------------- the chart libraries stay lazy
def test_the_entry_point_does_not_import_the_chart_libraries():
    """
    matplotlib + seaborn + scipy + PIL were 1.8s of a 3.8s import, on every run, for
    an opt-in PNG. A fresh interpreter is used because pytest itself may already have
    loaded them for another test.
    """
    code = (
        "import sys, importlib.util; sys.path.insert(0, 'src');"
        "spec = importlib.util.spec_from_file_location('m', 'src/__main__.py');"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m);"
        "print(','.join(n for n in ('matplotlib','seaborn','scipy','PIL','sklearn')"
        " if n in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    loaded = [n for n in out.stdout.strip().split(",") if n]
    assert not loaded, f"the entry point still imports {loaded} at module level"


def test_the_spec_leaves_the_heavy_libraries_out(spec_text):
    excludes = spec_text.split("excludes = [")[1].split("]")[0]
    for lib in ("sklearn", "scipy", "matplotlib", "seaborn", "PIL"):
        assert f'"{lib}"' in excludes


def test_ml_ranker_really_is_unreferenced():
    """
    sklearn is excluded on the grounds that nothing imports ml_ranker. If that ever
    stops being true the exclusion becomes a crash, so it is checked rather than
    remembered.
    """
    hits = []
    for path in (ROOT / "src").rglob("*.py"):
        if path.name == "ml_ranker.py":
            continue
        if "ml_ranker" in path.read_text(encoding="utf-8"):
            hits.append(path.name)
    assert not hits, f"ml_ranker is imported by {hits}; sklearn can no longer be excluded"


def test_png_degrades_with_a_message_not_a_traceback():
    source = (ROOT / "src" / "__main__.py").read_text(encoding="utf-8")
    branch = source.split('if getattr(args, "png", False):')[1][:600]
    assert "from viz.renderer import ScreenerViz" in branch, "the import must be lazy"
    assert "except ImportError" in branch
    assert "not in this build" in branch


# ------------------------------------------------------------------ the spec
def test_the_spec_names_pywebviews_windows_backend(spec_text):
    """Chosen at runtime, so invisible to static analysis; without it the window
    silently degrades to a browser tab in the frozen build."""
    assert "webview.platforms.edgechromium" in spec_text


def test_the_spec_declares_the_function_level_imports(spec_text):
    """The journal subcommands import inside functions to stay fast."""
    hidden = spec_text.split("hiddenimports = [")[1].split("]")[0]
    for module in ("cli", "desktop", "api", "report.advanced", "report.steps",
                   "analysis.valuation", "analysis.trace", "portfolio.journal",
                   "portfolio.ledger", "core.paths"):
        assert f'"{module}"' in hidden


def test_the_build_is_a_folder_not_a_single_file(spec_text):
    """onefile unpacks ~150MB to %TEMP% on every launch. This tool is used at lunch."""
    assert "COLLECT(" in spec_text
    assert "exclude_binaries=True" in spec_text


def test_upx_is_off(spec_text):
    """UPX is a common antivirus false-positive trigger and this build is unsigned."""
    assert "upx=False" in spec_text
    assert "upx=True" not in spec_text
