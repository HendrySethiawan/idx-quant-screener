# packaging/idx_terminal.spec -- PyInstaller build definition
#
# onedir, not onefile. A one-file exe unpacks its whole payload to %TEMP% on every
# launch, which would add 4-8 seconds to a tool whose entire point is a fast answer
# at lunch. A folder starts in about two seconds because nothing is unpacked.
#
# Run it through packaging/build.py rather than pyinstaller directly -- the build
# script does the personal-data scan afterwards, and that is not optional.
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent          # noqa: F821 (PyInstaller injects it)
SRC = ROOT / "src"

# ---------------------------------------------------------------------- datas
# Seed files copied out beside the exe on first run by core.paths.bootstrap().
#
# `configs/user.yaml`, `configs/events.yaml`, `current_holdings.yaml` and anything
# under `data/` are ABSENT ON PURPOSE. They hold the reader's capital, positions and
# trade history. Sweeping `configs/*` in here would put a private number inside every
# copy of a binary that might be handed to someone else, and nobody unzips an exe to
# check. packaging/build.py scans the finished tree and fails the build if any of
# them appear, so this comment is backed by something that actually runs.
datas = [
    (str(ROOT / "configs" / "default.yaml"), "configs"),
    (str(ROOT / "configs" / "events.example.yaml"), "configs"),
    (str(ROOT / "current_holdings.example.yaml"), "."),
]

# ------------------------------------------------------------- hidden imports
# The project imports a lot inside functions, to keep the journal subcommands from
# paying for the screener's imports. PyInstaller's static analysis finds most of it,
# but these are named explicitly so a missed one fails here rather than at runtime
# in front of the reader.
hiddenimports = [
    "cli", "desktop", "api", "runner", "first_run",
    "core.config", "core.logger", "core.paths",
    "analysis.fundamental", "analysis.selection", "analysis.technical",
    "analysis.trace", "analysis.valuation",
    "fetchers.data_fetcher",
    "market.events", "market.liquidity", "market.regime", "market.seasonality",
    "portfolio.cash", "portfolio.dividends", "portfolio.exits",
    "portfolio.fees", "portfolio.holdings", "portfolio.journal",
    "portfolio.ledger", "portfolio.performance", "portfolio.sizing",
    "report.advanced", "report.assemble", "report.brief", "report.charts",
    "report.explain", "report.journal_view", "report.layout", "report.steps",
    "report.terminal",
    "backtest.engine", "backtest.report",
    "pipeline",
    # pywebview's Windows backend is selected at runtime, so it is invisible to
    # static analysis. Without this the window silently falls back to a browser.
    "webview.platforms.edgechromium",
    "clr_loader",
    # yfinance reaches for these lazily.
    "peewee", "frozendict", "multitasking", "platformdirs", "curl_cffi",
]

# ------------------------------------------------------------------ excludes
# sklearn: src/analysis/ml_ranker.py imports it and nothing imports ml_ranker --
#   grep finds only that file's own header. Dead code, 41MB, plus scipy behind it.
# matplotlib / seaborn / PIL / fontTools / scipy: reached only by viz.renderer for
#   the opt-in --png chart. The import is lazy now, so leaving them out costs the
#   PNG in this build and nothing else. --png prints a message rather than a
#   traceback.
excludes = [
    "sklearn", "scipy", "matplotlib", "seaborn", "PIL", "fontTools",
    "tkinter", "pytest", "IPython", "notebook", "jupyter",
    "pandas.tests", "numpy.testing",
]

a = Analysis(                                    # noqa: F821
    [str(ROOT / "main.py")],
    pathex=[str(ROOT), str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)                                # noqa: F821

exe = EXE(                                       # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="IDX Terminal",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX compression is a common antivirus false-positive
                        # trigger, and this build is already unsigned.
    console=True,       # The console carries the run log and the printed ticket,
                        # and it is where --log / --journal are used. Hiding it
                        # would make a failed run look like nothing happened.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(                                  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="IDX Terminal",
)
