"""Legacy-install compatibility shims for the nanobot -> mason rename."""

from __future__ import annotations

from pathlib import Path

from mason.config import compat


def test_migrate_legacy_env_mirrors_only_unset_names() -> None:
    env = {"NANOBOT_VENV": "/venv", "MASON_SRC_DIR": "/explicit", "NANOBOT_SRC_DIR": "/legacy"}

    added = compat.migrate_legacy_env(env)

    assert set(added) == {"MASON_VENV"}
    assert env["MASON_VENV"] == "/venv"
    # An explicitly configured new name always wins over the legacy one.
    assert env["MASON_SRC_DIR"] == "/explicit"


def test_migrate_legacy_env_is_a_no_op_without_legacy_names() -> None:
    env = {"MASON_VENV": "/venv", "PATH": "/bin"}
    assert compat.migrate_legacy_env(env) == []
    assert env == {"MASON_VENV": "/venv", "PATH": "/bin"}


def test_migrate_legacy_data_dir_moves_data_and_keeps_install(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(compat, "_data_dir_checked", False)
    legacy = tmp_path / ".nanobot"
    (legacy / "workspace").mkdir(parents=True)
    (legacy / "config.json").write_text("{}", encoding="utf-8")
    (legacy / "src").mkdir()
    (legacy / "venv").mkdir()

    moved = compat.migrate_legacy_data_dir()

    assert moved == tmp_path / ".mason"
    assert (tmp_path / ".mason" / "config.json").read_text(encoding="utf-8") == "{}"
    assert (tmp_path / ".mason" / "workspace").is_dir()
    # The checkout and virtualenv stay where the install expects them.
    assert (legacy / "src").is_dir()
    assert (legacy / "venv").is_dir()
    assert not (legacy / "config.json").exists()
    # Once moved, the migration stays a no-op.
    monkeypatch.setattr(compat, "_data_dir_checked", False)
    assert compat.migrate_legacy_data_dir() is None


def test_migrate_legacy_data_dir_ignores_an_install_only_directory(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(compat, "_data_dir_checked", False)
    (tmp_path / ".nanobot" / "src").mkdir(parents=True)
    (tmp_path / ".nanobot" / "venv").mkdir()

    assert compat.migrate_legacy_data_dir() is None

    assert not (tmp_path / ".mason").exists()
    assert (tmp_path / ".nanobot" / "src").is_dir()


def test_migrate_legacy_data_dir_never_overwrites_existing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(compat, "_data_dir_checked", False)
    (tmp_path / ".nanobot" / "config.json").parent.mkdir(parents=True)
    (tmp_path / ".nanobot" / "config.json").write_text("legacy", encoding="utf-8")
    (tmp_path / ".mason").mkdir()

    assert compat.migrate_legacy_data_dir() is None

    assert (tmp_path / ".nanobot" / "config.json").exists()
