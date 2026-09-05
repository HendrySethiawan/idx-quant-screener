"""
Tests for path resolution from source and from a frozen build.

From source, every relative path in the project resolves against the repo root
because that is where you run it from. From a double-clicked exe the working
directory is whatever Windows chose, and `configs/user.yaml`, `data/cache` and
`logs/` would all land somewhere the reader would never find.

The one that matters most is test_bootstrap_never_overwrites_an_existing_user_yaml:
that file holds their capital, and a launcher that reset it on every run would be
worse than no launcher.

Second to it is the data_dir group. The reader's files used to live beside the exe,
which meant they lived in `dist/` -- the folder `packaging/build.py` deletes before
every build. Rebuilding the app erased the app's memory, repeatedly, and it took a
user asking "could this project have a memory?" to find it.
"""
import sys
from pathlib import Path

import pytest

from core import paths


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """
    Pretend to be a PyInstaller build living in tmp_path.

    LOCALAPPDATA is redirected into tmp_path too. Without that these tests write
    into the real per-user data folder -- which they did, once, filling it with
    fixture values before this line existed.
    """
    exe = tmp_path / "app" / "IDX Terminal.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    meipass = tmp_path / "bundle"
    meipass.mkdir()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    return exe.parent, meipass


@pytest.fixture
def home(tmp_path):
    """Where a frozen build keeps the reader's files, given the `frozen` fixture."""
    return tmp_path / "localappdata" / paths.APP_FOLDER_NAME


# ------------------------------------------------------------------ app_dir
def test_from_source_the_app_dir_is_the_repo_root():
    root = paths.app_dir()
    assert (root / "src").is_dir()
    assert (root / "configs" / "default.yaml").exists()
    assert paths.is_frozen() is False


def test_when_frozen_the_app_dir_follows_the_executable(frozen):
    app, _ = frozen
    assert paths.is_frozen() is True
    assert paths.app_dir() == app


def test_bundled_files_are_read_from_the_payload_when_frozen(frozen):
    _, meipass = frozen
    assert paths.bundled("configs/default.yaml") == meipass / "configs/default.yaml"


def test_bundled_files_come_from_the_repo_when_running_from_source():
    assert paths.bundled("configs/default.yaml").exists()


# ----------------------------------------------------------------- data_dir
def test_from_source_the_data_dir_is_the_repo_root():
    assert paths.data_dir() == paths.app_dir()


def test_when_frozen_the_data_dir_is_not_beside_the_executable(frozen, home):
    """The install folder is disposable. The reader's files are not."""
    app, _ = frozen
    assert paths.data_dir() == home
    assert paths.data_dir() != app
    assert app not in paths.data_dir().parents


def test_the_data_dir_survives_a_machine_with_no_localappdata(frozen, monkeypatch):
    """Failing to start is never the right answer to a missing environment variable."""
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert paths.data_dir().name == paths.APP_FOLDER_NAME


# ---------------------------------------------------------------- bootstrap
def test_bootstrap_creates_the_folders_the_app_writes_to(frozen, monkeypatch, home):
    app, _ = frozen
    monkeypatch.chdir(app.parent)

    created = paths.bootstrap()

    for rel in ("configs", "data", "data/output", "logs"):
        assert (home / rel).is_dir(), f"{rel} was not created"
    assert created


def test_bootstrap_anchors_the_working_directory_to_the_data_dir(
        frozen, monkeypatch, tmp_path, home):
    """
    The whole reason this exists: a double-clicked exe does not start in its own
    folder, and every path in the project is relative.
    """
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    paths.bootstrap()
    assert Path.cwd() == home


def test_bootstrap_copies_the_editable_config_out_of_the_bundle(frozen, monkeypatch, home):
    """A config you cannot edit is not a config."""
    app, meipass = frozen
    (meipass / "configs").mkdir()
    (meipass / "configs" / "default.yaml").write_text(
        "account:\n  capital_rp: 1\n", encoding="utf-8")
    monkeypatch.chdir(app.parent)

    paths.bootstrap()
    assert (home / "configs" / "default.yaml").read_text(
        encoding="utf-8").startswith("account:")


def test_bootstrap_never_overwrites_an_existing_user_yaml(frozen, monkeypatch, home):
    """
    That file holds the reader's capital. A launcher that reset it every run would
    be worse than no launcher at all.
    """
    (home / "configs").mkdir(parents=True)
    mine = home / "configs" / "user.yaml"
    mine.write_text("account:\n  capital_rp: 12345678\n", encoding="utf-8")
    monkeypatch.chdir(home.parent)

    paths.bootstrap()
    paths.bootstrap()

    assert "12345678" in mine.read_text(encoding="utf-8")


def test_bootstrap_never_creates_a_user_yaml_of_its_own(frozen, monkeypatch, home):
    """Nothing should invent a capital figure on the reader's behalf."""
    app, meipass = frozen
    (meipass / "configs").mkdir()
    (meipass / "configs" / "default.yaml").write_text("account:\n", encoding="utf-8")
    monkeypatch.chdir(app.parent)

    paths.bootstrap()
    assert not (home / "configs" / "user.yaml").exists()


def test_bootstrap_is_idempotent(frozen, monkeypatch, home):
    app, meipass = frozen
    (meipass / "configs").mkdir()
    (meipass / "configs" / "default.yaml").write_text("account:\n", encoding="utf-8")
    monkeypatch.chdir(app.parent)

    first = paths.bootstrap()
    second = paths.bootstrap()
    assert first and second == [], "a second run should have nothing left to do"


def test_bootstrap_survives_a_read_only_location(frozen, monkeypatch):
    """
    Being unable to write a copy of the defaults is not a reason to refuse to start:
    they are compiled in already.
    """
    app, meipass = frozen
    (meipass / "configs").mkdir()
    (meipass / "configs" / "default.yaml").write_text("account:\n", encoding="utf-8")
    monkeypatch.chdir(app.parent)

    def boom(*a, **k):
        raise OSError("read-only volume")
    monkeypatch.setattr(paths.shutil, "copyfile", boom)

    paths.bootstrap()          # must not raise


def test_bootstrap_from_source_does_not_move_the_working_directory(monkeypatch, tmp_path):
    """Only the frozen build needs anchoring; from source it must stay out of the way."""
    monkeypatch.chdir(tmp_path)
    paths.bootstrap()
    assert Path.cwd() == tmp_path


# ---------------------------------------------------------------- migration
def test_an_older_install_beside_the_exe_is_carried_across(frozen, monkeypatch, home):
    """Whoever already has files next to the exe must not lose them to this change."""
    app, _ = frozen
    (app / "configs").mkdir(parents=True)
    (app / "configs" / "user.yaml").write_text(
        "account:\n  capital_rp: 10000000\n", encoding="utf-8")
    (app / "data").mkdir()
    (app / "data" / "journal.csv").write_text(
        "date,ticker\n2026-08-20,BBRI.JK\n", encoding="utf-8")
    monkeypatch.chdir(app.parent)

    paths.bootstrap()

    assert "10000000" in (home / "configs" / "user.yaml").read_text(encoding="utf-8")
    assert "BBRI.JK" in (home / "data" / "journal.csv").read_text(encoding="utf-8")


def test_migration_never_overwrites_what_is_already_there(frozen, monkeypatch, home):
    """The per-user copy is the live one; an old install must not write over it."""
    app, _ = frozen
    (app / "configs").mkdir(parents=True)
    (app / "configs" / "user.yaml").write_text(
        "account:\n  capital_rp: 111\n", encoding="utf-8")
    (home / "configs").mkdir(parents=True)
    (home / "configs" / "user.yaml").write_text(
        "account:\n  capital_rp: 999\n", encoding="utf-8")
    monkeypatch.chdir(app.parent)

    paths.bootstrap()
    paths.bootstrap()

    assert "999" in (home / "configs" / "user.yaml").read_text(encoding="utf-8")


def test_migration_is_a_copy_so_the_old_install_still_works(frozen, monkeypatch, home):
    app, _ = frozen
    (app / "data").mkdir(parents=True)
    (app / "data" / "journal.csv").write_text("date\n2026-08-20\n", encoding="utf-8")
    monkeypatch.chdir(app.parent)

    paths.bootstrap()
    assert (app / "data" / "journal.csv").exists(), "migration must not delete anything"


def test_migration_from_source_does_nothing():
    """Source and destination are the same folder when running from the repo."""
    assert paths.migrate_from(paths.app_dir(), paths.app_dir()) == []


# ------------------------------------------------ a new build must reach the reader
# The bug these exist for had no symptom. `default.yaml` was seeded on first run and
# never again, so every later change to it -- the ticker universe, the broker fees,
# the factor weights, the IDX market rules -- was invisible to anyone running the
# exe. A build carrying 74 tickers analysed 49, because the copy from the first
# install was still sitting on disk. It would have swallowed the BEI Rp50 -> Rp1
# minimum-price change the same way.

def _bundle(meipass, text):
    (meipass / "configs").mkdir(exist_ok=True)
    (meipass / "configs" / "default.yaml").write_text(text, encoding="utf-8")


def test_a_new_build_replaces_the_config_it_seeded_before(frozen, monkeypatch, home):
    app, meipass = frozen
    _bundle(meipass, "stock_tickers:\n  BBRI.JK: Bank Rakyat\n")
    monkeypatch.chdir(app.parent)
    paths.bootstrap()

    shipped = home / "configs" / "default.yaml"
    assert "BBRI.JK" in shipped.read_text(encoding="utf-8")

    # The next release adds a name.
    _bundle(meipass, "stock_tickers:\n  BBRI.JK: Bank Rakyat\n  TPIA.JK: Chandra Asri\n")
    paths.bootstrap()

    assert "TPIA.JK" in shipped.read_text(encoding="utf-8"), (
        "a new build's config never reached the reader; this is how a universe of "
        "74 tickers went on screening 49")


def test_the_replaced_config_is_kept_alongside(frozen, monkeypatch, home):
    """A hand edit must be recoverable, even though user.yaml is the right place."""
    app, meipass = frozen
    _bundle(meipass, "max_per_sector: 2\n")
    monkeypatch.chdir(app.parent)
    paths.bootstrap()

    shipped = home / "configs" / "default.yaml"
    shipped.write_text("max_per_sector: 99   # my edit\n", encoding="utf-8")

    _bundle(meipass, "max_per_sector: 3\n")
    paths.bootstrap()

    assert "max_per_sector: 3" in shipped.read_text(encoding="utf-8")
    kept = home / "configs" / "default.yaml.replaced"
    assert kept.exists() and "my edit" in kept.read_text(encoding="utf-8")


def test_an_unchanged_config_is_left_completely_alone(frozen, monkeypatch, home):
    """
    No backup, no rewrite, nothing in the created list. Refreshing on every launch
    would litter the folder with `.replaced` files that record nothing.
    """
    app, meipass = frozen
    _bundle(meipass, "max_per_sector: 2\n")
    monkeypatch.chdir(app.parent)

    first = paths.bootstrap()
    second = paths.bootstrap()

    assert first and second == []
    assert not (home / "configs" / "default.yaml.replaced").exists()


def test_the_refresh_never_reaches_the_readers_own_files(frozen, monkeypatch, home):
    """
    The line that must not move. `user.yaml` holds their capital, `events.yaml`
    their diary and `current_holdings.yaml` their positions -- none is in the seed
    table, and adding one would be the same bug in the opposite direction.
    """
    app, meipass = frozen
    _bundle(meipass, "max_per_sector: 2\n")
    (home / "configs").mkdir(parents=True, exist_ok=True)
    mine = {
        home / "configs" / "user.yaml": "account:\n  capital_rp: 12345678\n",
        home / "configs" / "events.yaml": "events:\n  - ticker: BBRI.JK\n",
        home / "current_holdings.yaml": "holdings:\n  SRTG.JK: {lots: 10}\n",
    }
    for path, text in mine.items():
        path.write_text(text, encoding="utf-8")
    monkeypatch.chdir(app.parent)

    paths.bootstrap()
    _bundle(meipass, "max_per_sector: 3\n")
    paths.bootstrap()

    for path, text in mine.items():
        assert path.read_text(encoding="utf-8") == text, f"{path.name} was touched"


def test_only_app_owned_files_are_in_the_refresh_list():
    """
    Every entry is a shipped default or an `.example`. A file the reader edits by
    name -- user.yaml, events.yaml, current_holdings.yaml -- must never appear.
    """
    for src, dst, refresh in paths._SEED_FILES:
        assert refresh, f"{dst} is seeded but never refreshed"
        name = dst.rsplit("/", 1)[-1]
        assert name == "default.yaml" or ".example." in name, (
            f"{dst} looks like a file the reader owns; refreshing it would "
            f"overwrite their work")
