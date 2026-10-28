"""The tunnel gateway proxies exactly three paths; everything else must fall to 404."""

import re
from pathlib import Path

TEXT = (Path(__file__).parents[1] / "infra" / "Caddyfile").read_text(encoding="utf-8")

EXPECTED = {
    ("POST", "/api/v1/webhooks/datadog"),
    ("POST", "/api/v1/integrations/datadog/actions"),
    ("GET", "/api/v1/integrations/datadog/incidents"),
}


def matchers():
    found = {}
    for name, body in re.findall(r"^    @(\w+) \{\n(.*?)\n    \}", TEXT, re.S | re.M):
        method = re.search(r"method (\w+)", body).group(1)
        paths = re.findall(r"path (\S+)", body)
        found[name] = (method, paths)
    return found


def test_exactly_three_exact_paths_with_methods():
    pairs = set()
    for method, paths in matchers().values():
        assert len(paths) == 1 and "*" not in paths[0]  # exact paths, no wildcards
        pairs.add((method, paths[0]))
    assert pairs == EXPECTED


def test_only_matcher_handles_proxy_and_the_rest_is_404():
    blocks = re.findall(r"handle( @\w+)? \{(.*?)\n    \}", TEXT, re.S)
    named = [b for b in blocks if b[0]]
    assert sorted(b[0].strip() for b in named) == ["@actions", "@incidents", "@webhook"]
    for _, body in named:
        assert "reverse_proxy api:8000" in body
    fallback = [b for b in blocks if not b[0]]
    assert len(fallback) == 1
    assert 'respond "Not found" 404' in fallback[0][1] and "reverse_proxy" not in fallback[0][1]
    assert TEXT.count("reverse_proxy") == 3


def test_action_routes_limit_body_to_4kb():
    for name in ("actions", "incidents"):
        block = re.search(rf"handle @{name} \{{(.*?)\n    \}}", TEXT, re.S).group(1)
        assert "max_size 4KB" in block
