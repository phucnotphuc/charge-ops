import importlib.util
import io
from pathlib import Path

import pytest
from dotenv import dotenv_values

from chargeops.auth import verify_password
from chargeops.config import Settings

SPEC = importlib.util.spec_from_file_location(
    "init_env", Path(__file__).parents[1] / "scripts" / "init_env.py"
)
init_env = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(init_env)


def read_env(path):
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)


def test_default_env_has_secrets_and_origins_but_no_hash(tmp_path, capsys):
    target = tmp_path / ".env"
    init_env.main([], target=target)
    env = read_env(target)
    assert len(env["OPERATOR_SECRET"]) >= 24
    assert env["OPERATOR_PASSWORD_HASH"] == ""
    assert "http://localhost:8080" in env["ALLOWED_ORIGINS"]
    out = capsys.readouterr().out
    assert env["OPERATOR_SECRET"] not in out
    assert "delete" not in out.lower() and "OPERATOR_PASSWORD_HASH" in out


def test_operator_hash_is_single_quoted_and_read_back_by_settings(tmp_path, monkeypatch, capsys):
    target = tmp_path / ".env"
    monkeypatch.setattr("sys.stdin", io.StringIO("s3cret-pass\n"))
    init_env.main(["--operator-user", "ops", "--operator-password-stdin"], target=target)
    stored = read_env(target)["OPERATOR_PASSWORD_HASH"]
    assert stored.startswith("'scrypt$") and stored.endswith("'") and "$$" not in stored
    # pydantic-settings reads .env through python-dotenv: a single-quoted value is literal.
    assert dotenv_values(target)["OPERATOR_PASSWORD_HASH"] == stored[1:-1]
    for name in ("SIMULATOR_SECRET", "WEBHOOK_SECRET", "OPERATOR_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OPERATOR_PASSWORD_HASH", raising=False)
    config = Settings(_env_file=target)
    assert config.operator_username == "ops"
    assert verify_password("s3cret-pass", config.operator_password_hash.get_secret_value())
    out = capsys.readouterr().out
    assert "s3cret-pass" not in out and stored not in out


def test_existing_env_is_checked_before_prompting(tmp_path, monkeypatch, capsys):
    target = tmp_path / ".env"
    target.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(init_env.getpass, "getpass", lambda *_: pytest.fail("prompted despite existing .env"))
    with pytest.raises(SystemExit):
        init_env.main(["--operator-user", "ops"], target=target)
    assert target.read_text(encoding="utf-8") == "keep"
    out = capsys.readouterr().out
    assert "already exists" in out and "delete" not in out.lower()
    assert "OPERATOR_USERNAME" in out and "Datadog" in out


def test_stdin_flag_requires_user(tmp_path):
    with pytest.raises(SystemExit):
        init_env.main(["--operator-password-stdin"], target=tmp_path / ".env")


def test_env_quote_rejects_quotes():
    with pytest.raises(ValueError):
        init_env.env_quote("a'b")


def test_action_secret_generated_distinct_and_never_printed(tmp_path, capsys):
    target = tmp_path / ".env"
    init_env.main([], target=target)
    env = read_env(target)
    assert len(env["DATADOG_ACTION_SECRET"]) >= 24
    assert env["DATADOG_ACTION_SECRET"] not in {env["OPERATOR_SECRET"], env["WEBHOOK_SECRET"], env["SIMULATOR_SECRET"]}
    assert env["DATADOG_ACTION_SECRET"] not in capsys.readouterr().out

