"""How the API finds its database settings. No database needed."""

import pytest

from app.config import Settings, database_url_from_env

DB_VARS = ("DATABASE_URL", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "STORE")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in DB_VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_to_the_in_memory_store_without_a_database_url():
    s = Settings.from_env()
    assert s.store == "memory" and s.database_url is None


def test_database_url_wins_over_the_parts(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@h:1/d")
    monkeypatch.setenv("DB_USER", "other")
    monkeypatch.setenv("DB_PASSWORD", "other")
    assert database_url_from_env() == "postgresql://a:b@h:1/d"


def test_url_is_built_from_the_parts_alembic_uses(monkeypatch):
    monkeypatch.setenv("DB_USER", "rm_user")
    monkeypatch.setenv("DB_PASSWORD", "p@ss/w rd")
    monkeypatch.setenv("DB_PORT", "55433")
    assert database_url_from_env() == "postgresql://rm_user:p%40ss%2Fw%20rd@localhost:55433/responsematch"


def test_parts_without_a_user_and_password_give_no_url(monkeypatch):
    monkeypatch.setenv("DB_HOST", "somewhere")
    assert database_url_from_env() is None
