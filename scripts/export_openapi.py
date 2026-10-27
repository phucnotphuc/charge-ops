"""Write web/openapi.json from the FastAPI app without starting a server."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for name in ("SIMULATOR_SECRET", "WEBHOOK_SECRET", "OPERATOR_SECRET"):
    os.environ.setdefault(name, "openapi-export-placeholder-secret")

from chargeops.api import app  # noqa: E402


def export(target):
    target = Path(target)
    text = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


if __name__ == "__main__":
    print(f"Wrote {export(sys.argv[1] if len(sys.argv) > 1 else 'web/openapi.json')}")
