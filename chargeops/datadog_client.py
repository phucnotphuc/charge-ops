"""Minimal Datadog REST client for datadog-sync. Errors carry the HTTP status and a short reason only: never
headers, request bodies or response bodies (they can echo keys or webhook headers).

Endpoints (Datadog docs, 2026-10-28): the v2 Incidents API is "public beta"; no unstable-operation header is
needed for plain REST calls (only SDKs gate it). There is no writable timeline endpoint, so timeline steps are
mirrored as completed incident todos. Link attachments are marked Preview."""

import time
from datetime import UTC, datetime

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504}


class DatadogError(Exception):
    def __init__(self, method, path, status):
        super().__init__(f"Datadog {method} {path}: HTTP {status}")
        self.status = status


class DatadogClient:
    def __init__(self, api_key, app_key, site, transport=None, retries=3):
        self.site = site
        self.retries = retries
        self.http = httpx.Client(
            base_url=f"https://api.{site}",
            headers={"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key, "Content-Type": "application/json"},
            timeout=20,
            transport=transport,
        )

    def call(self, method, path, payload=None, params=None):
        for attempt in range(self.retries):
            try:
                response = self.http.request(method, path, json=payload, params=params)
            except httpx.HTTPError:
                # Transport errors can embed the request; surface status 0 only.
                if attempt < self.retries - 1:
                    time.sleep(2**attempt)
                    continue
                raise DatadogError(method, path, 0) from None
            if response.status_code in RETRY_STATUS and attempt < self.retries - 1:
                time.sleep(float(response.headers.get("Retry-After", 2**attempt)))
                continue
            if response.is_error:
                raise DatadogError(method, path, response.status_code)
            return response.json() if response.content else {}
        raise DatadogError(method, path, 0)

    def incident_url(self, public_id):
        return f"https://app.{self.site}/incidents/{public_id}"

    def create_incident(self, title, chargeops_incident_id):
        # The ChargeOps id stays in the title so find_incident can recover it after a restart.
        body = {
            "data": {
                "type": "incidents",
                "attributes": {
                    "title": f"{title} [{chargeops_incident_id}]",
                    "customer_impacted": False,
                    "fields": {"state": {"type": "dropdown", "value": "active"}},
                },
            }
        }
        data = self.call("POST", "/api/v2/incidents", body)["data"]
        return {"id": data["id"], "public_id": data["attributes"]["public_id"]}

    def incident_note(self, dd_incident_id, text):
        body = {
            "data": {
                "type": "incident_todos",
                "attributes": {
                    "assignees": [],
                    "content": text,
                    "completed": datetime.now(UTC).isoformat(),
                },
            }
        }
        self.call("POST", f"/api/v2/incidents/{dd_incident_id}/relationships/todos", body)

    def resolve_incident(self, dd_incident_id):
        body = {
            "data": {
                "id": dd_incident_id,
                "type": "incidents",
                "attributes": {"fields": {"state": {"type": "dropdown", "value": "resolved"}}},
            }
        }
        self.call("PATCH", f"/api/v2/incidents/{dd_incident_id}", body)

    def find_incident(self, chargeops_incident_id):
        result = self.call("GET", "/api/v2/incidents/search", params={"query": f'"{chargeops_incident_id}"'})
        for item in result.get("data", {}).get("attributes", {}).get("incidents", []):
            data = item["data"]
            return {"id": data["id"], "public_id": data["attributes"]["public_id"]}
        return None

    def attach_link(self, dd_incident_id, url, title):
        body = {
            "data": {
                "type": "incident_attachments",
                "attributes": {"attachment_type": "link", "attachment": {"documentUrl": url, "title": title}},
            }
        }
        self.call("POST", f"/api/v2/incidents/{dd_incident_id}/attachments", body)

    def create_notebook(self, payload):
        result = self.call("POST", "/api/v1/notebooks", {"data": {"type": "notebooks", "attributes": payload}})
        return result["data"]["id"]

    def update_notebook(self, notebook_id, payload):
        self.call("PUT", f"/api/v1/notebooks/{notebook_id}", {"data": {"type": "notebooks", "attributes": payload}})
