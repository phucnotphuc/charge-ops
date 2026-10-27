"""Create local-only secrets without printing them or overwriting existing configuration.

Optional operator login:  --operator-user NAME  (prompts for the password)
                          --operator-password-stdin  (read the password from stdin instead)
The scrypt hash is written with `$` doubled to `$$` so Compose does not interpolate it.
"""

import argparse
import getpass
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chargeops.auth import hash_password  # noqa: E402

DEFAULT_ORIGINS = "http://localhost:8080,http://127.0.0.1:8080,http://localhost:5173"


def compose_escape(value):
    return value.replace("$", "$$")


def main(argv=None, target=Path(".env")):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--operator-user", metavar="NAME")
    parser.add_argument("--operator-password-stdin", action="store_true")
    args = parser.parse_args(argv)
    if args.operator_password_stdin and not args.operator_user:
        parser.error("--operator-password-stdin requires --operator-user")

    username, password_hash = "operator", ""
    if args.operator_user:
        username = args.operator_user
        if args.operator_password_stdin:
            password = sys.stdin.readline().rstrip("\r\n")
        else:
            password = getpass.getpass("Operator password: ")
        if not password:
            parser.error("operator password must not be empty")
        password_hash = compose_escape(hash_password(password))

    with target.open("x", encoding="utf-8") as file:
        file.write(f"SIMULATOR_SECRET={secrets.token_urlsafe(32)}\n")
        file.write(f"WEBHOOK_SECRET={secrets.token_urlsafe(32)}\n")
        file.write(f"OPERATOR_SECRET={secrets.token_urlsafe(32)}\n")
        file.write(
            "DD_API_KEY=\nDD_APP_KEY=\nDD_SITE=datadoghq.com\nDD_MONITOR_ID=\nDD_TRIAL_ID=hackathon-2026\n"
        )
        file.write(f"OPERATOR_USERNAME={username}\nOPERATOR_PASSWORD_HASH={password_hash}\n")
        file.write(f"ALLOWED_ORIGINS={DEFAULT_ORIGINS}\nDD_TRACE_ENABLED=false\nAPP_VERSION=0.1.0\n")
    print("Created .env with separate local secrets. Configure Datadog keys locally; do not share them.")
    if args.operator_user:
        print(f"Operator login configured for user '{username}'.")
    else:
        print("No operator login yet: delete .env and rerun with --operator-user NAME, or set the hash by hand.")


if __name__ == "__main__":
    main()
