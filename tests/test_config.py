from pathlib import Path

import pytest

from app.core import config


def write_env_file(path: Path, *, env: str, database_url: str, jwt_secret: str) -> None:
    path.write_text(
        "\n".join(
            [
                f"ENV={env}",
                f"DATABASE_URL={database_url}",
                f"JWT_SECRET={jwt_secret}",
                "JWT_ALG=HS256",
            ]
        ),
        encoding="utf-8",
    )


def clear_runtime_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("ENV", "DATABASE_URL", "JWT_SECRET", "JWT_ALG", "LOG_LEVEL"):
        monkeypatch.delenv(key, raising=False)


def test_resolve_env_profile_defaults_to_dev():
    assert config.resolve_env_profile() == "dev"
    assert config.resolve_env_profile(None) == "dev"


def test_resolve_env_profile_rejects_unknown():
    with pytest.raises(ValueError):
        config.resolve_env_profile("qa")


def test_get_settings_reads_explicit_profile_file(repo_tmp_dir, monkeypatch):
    clear_runtime_overrides(monkeypatch)
    env_file = repo_tmp_dir / "config" / ".env.stage"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    write_env_file(
        env_file,
        env="stage",
        database_url="sqlite:///./stage.db",
        jwt_secret="stage-secret",
    )

    config.get_settings.cache_clear()
    loaded = config.get_settings("stage", env_file=env_file)

    assert loaded.ENV == "stage"
    assert loaded.DATABASE_URL == "sqlite:///./stage.db"
    assert loaded.JWT_SECRET == "stage-secret"

    config.get_settings.cache_clear()


def test_get_settings_normalizes_log_level(repo_tmp_dir, monkeypatch):
    clear_runtime_overrides(monkeypatch)
    env_file = repo_tmp_dir / "config" / ".env.dev"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        "\n".join(
            [
                "ENV=dev",
                "DATABASE_URL=sqlite:///./dev.db",
                "JWT_SECRET=dev-secret",
                "LOG_LEVEL=debug",
            ]
        ),
        encoding="utf-8",
    )

    config.get_settings.cache_clear()
    loaded = config.get_settings("dev", env_file=env_file)

    assert loaded.LOG_LEVEL == "DEBUG"

    config.get_settings.cache_clear()
