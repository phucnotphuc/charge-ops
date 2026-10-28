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
    """name -> (method, exact path) from `@name expression `{method} == 'M' && {http.request.uri.path} == '/p'``."""
    found = {}
    pattern = r"^    @(\w+) expression `\{method\} == '([A-Z]+)' && \{http\.request\.uri\.path\} == '([^']+)'`$"
    for line in TEXT.splitlines():
        if line.strip().startswith("@"):
            match = re.match(pattern, line)
            assert match, f"matcher must be an exact, case-sensitive expression: {line!r}"
            found[match.group(1)] = (match.group(2), match.group(3))
    return found


def test_exactly_three_exact_paths_with_methods():
    found = matchers()
    assert set(found) == {"webhook", "actions", "incidents"}
    assert set(found.values()) == EXPECTED
    assert not re.search(r"^\s*(path|path_regexp|method)\s", TEXT, re.M)  # no case-insensitive/glob matchers
    assert all("*" not in path for _, path in found.values())


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
