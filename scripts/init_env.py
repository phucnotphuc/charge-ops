"""Create local-only secrets without printing them or overwriting existing configuration."""

import secrets
from pathlib import Path

target = Path(".env")
with target.open("x", encoding="utf-8") as file:
    file.write(f"SIMULATOR_SECRET={secrets.token_urlsafe(32)}\n")
    file.write(f"WEBHOOK_SECRET={secrets.token_urlsafe(32)}\n")
    file.write(f"OPERATOR_SECRET={secrets.token_urlsafe(32)}\n")
    file.write(
        "DD_API_KEY=\nDD_APP_KEY=\nDD_SITE=datadoghq.com\nDD_MONITOR_ID=\nDD_TRIAL_ID=hackathon-2026\n"
    )
print("Created .env with separate local secrets. Configure Datadog keys locally; do not share them.")
