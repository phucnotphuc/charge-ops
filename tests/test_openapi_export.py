import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("export_openapi", ROOT / "scripts" / "export_openapi.py")
export_openapi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(export_openapi)


def test_export_matches_committed_openapi(tmp_path):
    out = export_openapi.export(tmp_path / "openapi.json")
    assert json.loads(out.read_text(encoding="utf-8"))["paths"]["/api/v1/connectors"]
    assert out.read_text(encoding="utf-8") == (ROOT / "web" / "openapi.json").read_text(encoding="utf-8")
