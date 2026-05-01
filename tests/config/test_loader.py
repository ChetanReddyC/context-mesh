"""Tests for `context_mesh.config.load_config` and `Mesh.from_config`."""

from __future__ import annotations

from pathlib import Path

import pytest

from context_mesh.api import Mesh
from context_mesh.config import (
    Config,
    ConfigError,
    load_config,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point HOME at tmp_path and clear env-var overrides before every test."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("CONTEXT_MESH_DB", raising=False)
    monkeypatch.delenv("CONTEXT_MESH_LOG_LEVEL", raising=False)
    monkeypatch.delenv("CONTEXT_MESH_HUB_URL", raising=False)
    # `Path.home()` is captured at import time for DEFAULT_GLOBAL_CONFIG_PATH;
    # patch the live attribute used by the loader.
    from context_mesh.config import loader as loader_module

    monkeypatch.setattr(
        loader_module,
        "DEFAULT_GLOBAL_CONFIG_PATH",
        Path(tmp_path) / ".context-mesh" / "config.toml",
    )


def _write_global(tmp_path: Path, text: str) -> Path:
    p = tmp_path / ".context-mesh" / "config.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _write_project(project: Path, text: str) -> Path:
    p = project / ".context-mesh" / "config.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_defaults_when_no_files_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    cfg = load_config()
    assert isinstance(cfg, Config)
    assert cfg.storage.path == ".context-mesh/memory.db"
    assert cfg.embeddings.provider == "huggingface"
    assert cfg.embeddings.dimensions == 384
    assert cfg.retrieval.default_limit == 5
    assert cfg.scopes.default_scope == "private"
    assert cfg.source_paths == ()


def test_global_config_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_global(
        tmp_path,
        '[storage]\npath = "/tmp/global.db"\n',
    )
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    cfg = load_config()
    assert cfg.storage.path == "/tmp/global.db"
    assert len(cfg.source_paths) == 1


def test_project_config_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project(project, '[storage]\npath = "proj.db"\n')
    monkeypatch.chdir(project)
    cfg = load_config()
    assert cfg.storage.path == "proj.db"
    assert len(cfg.source_paths) == 1


def test_project_overrides_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_global(
        tmp_path,
        '[storage]\npath = "/tmp/global.db"\nbackup_dir = "/tmp/bak"\n',
    )
    project = tmp_path / "proj"
    project.mkdir()
    _write_project(project, '[storage]\npath = "proj.db"\n')
    monkeypatch.chdir(project)
    cfg = load_config()
    assert cfg.storage.path == "proj.db"
    # backup_dir comes from global, not overridden by project
    assert cfg.storage.backup_dir == "/tmp/bak"
    assert len(cfg.source_paths) == 2


def test_env_var_overrides_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project(project, '[storage]\npath = "proj.db"\n')
    monkeypatch.chdir(project)
    monkeypatch.setenv("CONTEXT_MESH_DB", "/env/value.db")
    cfg = load_config()
    assert cfg.storage.path == "/env/value.db"


def test_env_var_log_level_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONTEXT_MESH_LOG_LEVEL", "debug")
    cfg = load_config()
    assert cfg.observability.log_level == "debug"


def test_env_var_log_level_invalid_value_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONTEXT_MESH_LOG_LEVEL", "verbose")
    with pytest.raises(ConfigError):
        load_config()


def test_env_var_hub_url_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CONTEXT_MESH_HUB_URL", "https://example.com")
    cfg = load_config()
    assert cfg.sync.hub_url == "https://example.com"


def test_invalid_toml_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project(project, "this is not = valid = toml [[[[\n")
    monkeypatch.chdir(project)
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config()


def test_invalid_type_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project(project, '[embeddings]\ndimensions = "not an int"\n')
    monkeypatch.chdir(project)
    with pytest.raises(ConfigError, match="dimensions"):
        load_config()


def test_invalid_enum_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project(project, '[scopes]\ndefault_scope = "public"\n')
    monkeypatch.chdir(project)
    with pytest.raises(ConfigError, match="default_scope"):
        load_config()


def test_unknown_section_warns_and_loads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project(
        project,
        '[unknown_section]\nfoo = "bar"\n[storage]\npath = "ok.db"\n',
    )
    monkeypatch.chdir(project)
    cfg = load_config()
    # The config still loads with the known section applied.
    assert cfg.storage.path == "ok.db"


def test_unknown_key_warns_and_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project(
        project,
        '[storage]\npath = "ok.db"\nmystery_key = 7\n',
    )
    monkeypatch.chdir(project)
    cfg = load_config()
    assert cfg.storage.path == "ok.db"


def test_source_paths_order_low_to_high(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    g = _write_global(tmp_path, '[storage]\npath = "g.db"\n')
    project = tmp_path / "proj"
    project.mkdir()
    p = _write_project(project, '[storage]\npath = "p.db"\n')
    monkeypatch.chdir(project)
    cfg = load_config()
    assert cfg.source_paths == (g, p)


def test_use_global_skips_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_global(tmp_path, '[storage]\npath = "g.db"\n')
    project = tmp_path / "proj"
    project.mkdir()
    _write_project(project, '[storage]\npath = "p.db"\n')
    monkeypatch.chdir(project)
    cfg = load_config(use_global=True)
    assert cfg.storage.path == "g.db"
    assert len(cfg.source_paths) == 1


def test_path_arg_overrides_project_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project(project, '[storage]\npath = "p.db"\n')
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    (explicit / "config.toml").write_text('[storage]\npath = "explicit.db"\n', encoding="utf-8")
    monkeypatch.chdir(project)
    cfg = load_config(path=explicit)
    assert cfg.storage.path == "explicit.db"


def test_mesh_from_config_opens_db(tmp_path: Path) -> None:
    db_path = tmp_path / "from_config.db"
    cfg = Config(
        storage=Config().storage.__class__(path=str(db_path), backup_dir=str(tmp_path)),
    )
    mesh = Mesh.from_config(cfg)
    try:
        # Connection should be live; a no-op SELECT proves it.
        rows = mesh.connection.execute("SELECT 1").fetchall()
        assert rows == [(1,)]
    finally:
        mesh.close()
    assert db_path.exists()
