import importlib.util
import io
from pathlib import Path

import pytest

from chargeops.auth import verify_password

SPEC = importlib.util.spec_from_file_location("init_env", Path(__file__).parents[1] / "scripts" / "init_env.py")
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
    assert env["OPERATOR_SECRET"] not in capsys.readouterr().out


def test_operator_hash_is_dollar_escaped_and_verifies(tmp_path, monkeypatch, capsys):
    target = tmp_path / ".env"
    monkeypatch.setattr("sys.stdin", io.StringIO("s3cret-pass\n"))
    init_env.main(["--operator-user", "ops", "--operator-password-stdin"], target=target)
    env = read_env(target)
    stored = env["OPERATOR_PASSWORD_HASH"]
    assert env["OPERATOR_USERNAME"] == "ops"
    assert stored.startswith("scrypt$$") and "$" not in stored.replace("$$", "")
    assert verify_password("s3cret-pass", stored.replace("$$", "$"))
    out = capsys.readouterr().out
    assert "s3cret-pass" not in out and stored not in out


def test_never_overwrites_existing_env(tmp_path):
    target = tmp_path / ".env"
    target.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        init_env.main([], target=target)
    assert target.read_text(encoding="utf-8") == "keep"


def test_stdin_flag_requires_user(tmp_path):
    with pytest.raises(SystemExit):
        init_env.main(["--operator-password-stdin"], target=tmp_path / ".env")
