"""
Tests for path resolution from source and from a frozen build.

From source, every relative path in the project resolves against the repo root
because that is where you run it from. From a double-clicked exe the working
directory is whatever Windows chose, and `configs/user.yaml`, `data/cache` and
`logs/` would all land somewhere the reader would never find.

The one that matters most is test_bootstrap_never_overwrites_an_existing_user_yaml:
that file holds their capital, and a launcher that reset it on every run would be
worse than no launcher.
"""
import sys
from pathlib import Path

import pytest

from core import paths


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Pretend to be a PyInstaller build living in tmp_path."""
    exe = tmp_path / "app" / "IDX Terminal.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    meipass = tmp_path / "bundle"
    meipass.mkdir()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    return exe.parent, meipass


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


# ---------------------------------------------------------------- bootstrap
def test_bootstrap_creates_the_folders_the_app_writes_to(frozen, monkeypatch):
    app, meipass = frozen
    monkeypatch.chdir(app.parent)

    created = paths.bootstrap()

    for rel in ("configs", "data", "data/output", "logs"):
        assert (app / rel).is_dir(), f"{rel} was not created"
    assert created


def test_bootstrap_anchors_the_working_directory_to_the_exe(frozen, monkeypatch, tmp_path):
    """
    The whole reason this exists: a double-clicked exe does not start in its own
    folder, and every path in the project is relative.
    """
    app, _ = frozen
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    paths.bootstrap()
    assert Path.cwd() == app


def test_bootstrap_copies_the_editable_config_out_of_the_bundle(frozen, monkeypatch):
    """A config you cannot edit is not a config."""
    app, meipass = frozen
    (meipass / "configs").mkdir()
    (meipass / "configs" / "default.yaml").write_text("account:\n  capital_rp: 1\n",
                                                      encoding="utf-8")
    monkeypatch.chdir(app.parent)

    paths.bootstrap()
    assert (app / "configs" / "default.yaml").read_text(encoding="utf-8").startswith("account:")


def test_bootstrap_never_overwrites_an_existing_user_yaml(frozen, monkeypatch):
    """
    That file holds the reader's capital. A launcher that reset it every run would
    be worse than no launcher at all.
    """
    app, _ = frozen
    (app / "configs").mkdir(parents=True)
    mine = app / "configs" / "user.yaml"
    mine.write_text("account:\n  capital_rp: 12345678\n", encoding="utf-8")
    monkeypatch.chdir(app.parent)

    paths.bootstrap()
    paths.bootstrap()

    assert "12345678" in mine.read_text(encoding="utf-8")


def test_bootstrap_never_creates_a_user_yaml_of_its_own(frozen, monkeypatch):
    """Nothing should invent a capital figure on the reader's behalf."""
    app, meipass = frozen
    (meipass / "configs").mkdir()
    (meipass / "configs" / "default.yaml").write_text("account:\n", encoding="utf-8")
    monkeypatch.chdir(app.parent)

    paths.bootstrap()
    assert not (app / "configs" / "user.yaml").exists()


def test_bootstrap_is_idempotent(frozen, monkeypatch):
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
