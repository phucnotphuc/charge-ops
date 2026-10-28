import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../api/auth";
import { DemoControl } from "../pages/DemoControl";
import { Evaluations } from "../pages/Evaluations";
import { IncidentDetail } from "../pages/IncidentDetail";
import { Overview } from "../pages/Overview";
import { api, MAX_TELEMETRY_PAGES } from "../api/client";
import { downsample } from "../lib/format";
import { commandAllowed, latestAppliedReduction, reductionCheckText } from "../pages/IncidentDetail";
import type { Incident } from "../api/types";
import { connector, evaluation, incident } from "./fixtures";

type Handler = (url: string, init?: RequestInit) => { status?: number; body?: unknown };

function mockFetch(handler: Handler) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const { status = 200, body } = handler(String(input), init);
    return new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const me = (role: string) => ({ body: { username: "ops", role } });
const unauthenticated = {
  status: 401,
  body: { error: { code: "unauthenticated", message: "Chưa đăng nhập", request_id: "r0" } },
};

function renderAt(path: string, ui: ReactElement, route = path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <Routes>
          <Route path={route} element={ui} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.stubGlobal("crypto", { randomUUID: () => "11111111-2222-4333-8444-555555555555" });
});
afterEach(() => vi.unstubAllGlobals());

describe("incident screen", () => {
  it.each([
    ["residual_not_recovered", true],
    ["load_not_reduced", false],
    ["insufficient_data", false],
    [null, false],
  ])("gates maintenance during mitigation for reason %s", async (reason, allowed) => {
    mockFetch((url) => {
      if (url.endsWith("/auth/me")) return me("operator");
      if (url.endsWith("/incidents/inc-1")) return {
        body: { ...incident({ state: "mitigating" }), reduction_check_reason: reason },
      };
      return { status: 404 };
    });
    renderAt("/incidents/inc-1", <IncidentDetail />, "/incidents/:id");
    const button = await screen.findByRole("button", { name: "Bảo trì" });
    await waitFor(() => expect(screen.queryByTestId("readonly-note")).not.toBeInTheDocument());
    if (allowed) expect(button).toBeEnabled();
    else expect(button).toBeDisabled();
  });

  it("shows Datadog links only when present", async () => {
    const withLinks = {
      ...incident(),
      datadog_incident_url: "https://app.datadoghq.com/incidents/7",
      datadog_notebook_url: "https://app.datadoghq.com/notebook/12",
    };
    mockFetch((url) => {
      if (url.endsWith("/auth/me")) return unauthenticated;
      if (url.endsWith("/incidents/inc-1")) return { body: withLinks };
      return { status: 404 };
    });
    const first = renderAt("/incidents/inc-1", <IncidentDetail />, "/incidents/:id");
    const open = await screen.findByRole("link", { name: "Mở trên Datadog" });
    expect(open).toHaveAttribute("href", withLinks.datadog_incident_url);
    expect(open).toHaveAttribute("target", "_blank");
    expect(open).toHaveAttribute("rel", "noreferrer");
    expect(screen.getByRole("link", { name: "Notebook điều tra" })).toHaveAttribute(
      "href",
      withLinks.datadog_notebook_url,
    );
    first.unmount();

    mockFetch((url) => {
      if (url.endsWith("/auth/me")) return unauthenticated;
      if (url.endsWith("/incidents/inc-1")) return { body: incident() };
      return { status: 404 };
    });
    renderAt("/incidents/inc-1", <IncidentDetail />, "/incidents/:id");
    await screen.findByTestId("state-badge");
    expect(screen.queryByRole("link", { name: "Mở trên Datadog" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Notebook điều tra" })).not.toBeInTheDocument();
  });

  it("renders state, source and the two-step proof, and disables commands for a viewer", async () => {
    mockFetch((url) => {
      if (url.endsWith("/auth/me")) return unauthenticated;
      if (url.endsWith("/incidents/inc-1")) return { body: incident({ state: "mitigating" }) };
      return { status: 404 };
    });
    renderAt("/incidents/inc-1", <IncidentDetail />, "/incidents/:id");

    expect(await screen.findByTestId("state-badge")).toHaveTextContent("Đang giảm tải");
    expect(screen.getByText("Nguồn: Datadog")).toBeInTheDocument();
    expect(screen.getByText("Đã áp dụng lệnh (ACK)")).toBeInTheDocument();
    expect(screen.getByText("Đã giảm dòng (đã kiểm chứng bằng telemetry)")).toBeInTheDocument();
    expect(screen.getByText("Mở sự cố")).toBeInTheDocument();
    for (const name of ["Xác nhận sự cố", "Giảm tải", "Bảo trì", "Kiểm tra phục hồi"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }
    expect(screen.getByTestId("readonly-note")).toBeInTheDocument();
  });

  it("sends Idempotency-Key with credentials when an operator clicks a command", async () => {
    const fetchMock = mockFetch((url, init) => {
      if (url.endsWith("/auth/me")) return me("operator");
      if (url.endsWith("/incidents/inc-1/commands") && init?.method === "POST")
        return { status: 202, body: { command_id: "cmd-1", status: "pending" } };
      if (url.endsWith("/commands/cmd-1")) return { body: { id: "cmd-1", type: "reduce_load", status: "applied" } };
      if (url.endsWith("/incidents/inc-1")) return { body: incident({ state: "acknowledged" }) };
      return { status: 404 };
    });
    renderAt("/incidents/inc-1", <IncidentDetail />, "/incidents/:id");

    const button = await screen.findByRole("button", { name: "Giảm tải" });
    await waitFor(() => expect(button).toBeEnabled());
    expect(screen.getByRole("button", { name: "Bảo trì" })).toBeDisabled();
    fireEvent.click(button);

    await waitFor(() => expect(screen.getByTestId("tracked-command")).toHaveTextContent("Đã áp dụng lệnh (ACK)"));
    const call = fetchMock.mock.calls.find(([u, i]) => String(u).endsWith("/commands") && i?.method === "POST")!;
    const init = call[1]!;
    expect(init.credentials).toBe("include");
    expect((init.headers as Record<string, string>)["Idempotency-Key"]).toBe("11111111-2222-4333-8444-555555555555");
    expect(JSON.parse(String(init.body))).toEqual({ type: "reduce_load" });
    // ACK must not be presented as a verified reduction.
    expect(screen.getByTestId("tracked-command")).toHaveTextContent("ACK chưa phải bằng chứng đã giảm dòng");
  });

  it("unlocks maintenance in mitigating only after residual_not_recovered, like the backend", async () => {
    mockFetch((url) => {
      if (url.endsWith("/auth/me")) return me("operator");
      if (url.endsWith("/incidents/inc-1"))
        return {
          body: incident({
            state: "mitigating",
            load_reduction_verified_at: "2026-10-21T00:05:00Z",
            reduction_check_reason: "residual_not_recovered",
          }),
        };
      return { status: 404 };
    });
    renderAt("/incidents/inc-1", <IncidentDetail />, "/incidents/:id");

    const maintenance = await screen.findByRole("button", { name: "Bảo trì" });
    await waitFor(() => expect(maintenance).toBeEnabled());
    // Derating already verified: the backend rejects another reduce_load.
    expect(screen.getByRole("button", { name: "Giảm tải" })).toBeDisabled();
  });

  it("shows an error state, not mock data, when the API fails", async () => {
    mockFetch((url) => {
      if (url.endsWith("/auth/me")) return unauthenticated;
      return {
        status: 500,
        body: { error: { code: "internal", message: "Cơ sở dữ liệu không khả dụng", request_id: "req-9" } },
      };
    });
    renderAt("/incidents/inc-1", <IncidentDetail />, "/incidents/:id");

    expect(await screen.findByRole("alert")).toHaveTextContent("Cơ sở dữ liệu không khả dụng");
    expect(screen.getByRole("alert")).toHaveTextContent("req-9");
    expect(screen.queryByTestId("state-badge")).not.toBeInTheDocument();
  });
});

describe("overview", () => {
  it("highlights a stale connector and keeps a fresh one unhighlighted", async () => {
    mockFetch((url) => {
      if (url.endsWith("/auth/me")) return unauthenticated;
      if (url.endsWith("/connectors"))
        return {
          body: {
            items: [
              connector({ connector_id: "fresh", freshness_s: 5, stale: false }),
              connector({ connector_id: "old", freshness_s: 45, stale: true }),
              connector({ connector_id: "none", freshness_s: null, stale: true, latest: null, measured_at: null }),
            ],
            next_cursor: null,
          },
        };
      return { status: 404 };
    });
    renderAt("/", <Overview />);

    const old = await screen.findByTestId("connector-old");
    expect(old).toHaveClass("stale");
    expect(old).toHaveTextContent("Dữ liệu cũ");
    expect(screen.getByTestId("connector-fresh")).not.toHaveClass("stale");
    expect(screen.getByTestId("connector-none")).toHaveClass("stale");
    expect(screen.getByTestId("connector-none")).toHaveTextContent("Chưa có telemetry");
  });

  it("shows units and an error state when connectors cannot be loaded", async () => {
    mockFetch((url) => {
      if (url.endsWith("/auth/me")) return unauthenticated;
      return { status: 503, body: { error: { code: "unavailable", message: "Dịch vụ tạm ngừng", request_id: "r1" } } };
    });
    renderAt("/", <Overview />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Dịch vụ tạm ngừng");
    expect(screen.queryByTestId(/^connector-/)).not.toBeInTheDocument();
  });

  it("shows an empty state for no connectors", async () => {
    mockFetch((url) => (url.endsWith("/auth/me") ? unauthenticated : { body: { items: [], next_cursor: null } }));
    renderAt("/", <Overview />);
    expect(await screen.findByTestId("empty")).toBeInTheDocument();
  });
});

describe("demo control and evaluations", () => {
  it("disables run controls for a viewer", async () => {
    mockFetch((url) => {
      if (url.endsWith("/auth/me")) return unauthenticated;
      if (url.endsWith("/demo-runs"))
        return {
          body: {
            items: [
              { _id: "run-abcdef12", status: "active", mode: "datadog", scenario: "thermal_fault", seed: 7 },
              { _id: "run-rejected", status: "rejected", mode: "datadog", scenario: "healthy", seed: 1, reason: "trial_run_budget_exhausted" },
              { _id: "run-cancelled", status: "cancelled", mode: "local", scenario: "healthy", seed: 2 },
            ],
            next_cursor: null,
          },
        };
      if (url.endsWith("/connectors")) return { body: { items: [connector()], next_cursor: null } };
      return { status: 404 };
    });
    renderAt("/demo", <DemoControl />);
    expect(await screen.findByTestId("run-reason")).toHaveTextContent("20 run ID Datadog");
    expect(screen.getByText("Đã hủy")).toBeInTheDocument();
    const scenarios = Array.from(screen.getByLabelText("Kịch bản").querySelectorAll("option")).map((o) => o.value);
    expect(scenarios).toEqual(["healthy", "load_increase", "demand_drop", "thermal_fault"]);
    const labels = Array.from(screen.getByLabelText("Kịch bản").querySelectorAll("option")).map((o) => o.textContent);
    expect(labels).toEqual(["Khỏe mạnh", "Tăng tải", "Giảm nhu cầu", "Lỗi nhiệt"]);
    expect(screen.getByRole("button", { name: "Tạo run" })).toBeDisabled();
    screen.getAllByRole("button", { name: "Dừng" }).forEach((b) => expect(b).toBeDisabled());
    expect(screen.getByRole("button", { name: "Chèn lỗi" })).toBeDisabled();
  });

  it("treats a missing /evaluations endpoint as an empty state", async () => {
    mockFetch((url) => (url.endsWith("/auth/me") ? unauthenticated : { status: 404, body: { error: { code: "not_found", message: "x", request_id: "r" } } }));
    renderAt("/evaluations", <Evaluations />);
    expect(await screen.findByTestId("empty")).toHaveTextContent("Chưa có kết quả đánh giá");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByTestId("sim-disclaimer")).toHaveTextContent("Chỉ là mô phỏng");
  });

  it("renders arms, detectors and targets from the committed summary shape", async () => {
    const ev = evaluation();
    const { summary_markdown: _md, healthy_connector_hours: _h, arms: _a, detectors: _d, ...item } = ev;
    mockFetch((url) => {
      if (url.endsWith("/auth/me")) return unauthenticated;
      if (url.endsWith("/evaluations")) return { body: { items: [item], next_cursor: null } };
      if (url.endsWith("/evaluations/b1-eval-v3.1")) return { body: ev };
      return { status: 404 };
    });
    renderAt("/evaluations", <Evaluations />);
    const arms = await screen.findByTestId("arms");
    expect(screen.getByTestId("sim-disclaimer")).toHaveTextContent("không phải kết quả chạy thật");
    expect(arms).toHaveTextContent("C: ChargeOps");
    expect(arms).toHaveTextContent("155 [100–455] (n=20)");
    expect(arms).toHaveTextContent("— (n=0)"); // B has no mitigation: missing, never 0
    expect(arms).toHaveTextContent("-22.54");
    const detectors = screen.getByTestId("detectors");
    expect(detectors).toHaveTextContent("Residual theo tải");
    expect(detectors).toHaveTextContent("415 [230–740] (n=10)");
    expect(screen.getByTestId("targets")).toHaveTextContent("C phát hiện ≥18/20 lỗi trước khi B trip: đạt");
    expect(screen.getByRole("button", { name: "b1-eval-v3.1" })).toBeInTheDocument();
  });
});

describe("real backend shapes", () => {
  it("shows the active incident state on the connector card", async () => {
    mockFetch((url) =>
      url.endsWith("/auth/me")
        ? unauthenticated
        : { body: { items: [connector({ active_incident: { id: "inc-7", state: "mitigating" } })], next_cursor: null } },
    );
    renderAt("/", <Overview />);
    const card = await screen.findByTestId("connector-connector-1");
    expect(card).toHaveTextContent("Có sự cố đang xử lý");
    expect(card).toHaveTextContent("Đang giảm tải");
    expect(card.querySelector("a[href='/incidents/inc-7']")).not.toBeNull();
  });

  it("does not crash when provenance and status fields are missing or null", async () => {
    const base = connector();
    mockFetch((url) =>
      url.endsWith("/auth/me")
        ? unauthenticated
        : {
            body: {
              items: [
                connector({ latest: { ...base.latest!, provenance: null } }),
                connector({ connector_id: "no-prov", latest: { ...base.latest!, provenance: undefined, connector_status: undefined } }),
                connector({ connector_id: "no-station", station_id: null, charger_id: null }),
              ],
              next_cursor: null,
            },
          },
    );
    renderAt("/", <Overview />);
    expect(await screen.findByTestId("connector-no-prov")).toHaveTextContent("manifest —");
    expect(screen.getByTestId("connector-no-station")).toHaveTextContent("Trạm — · Trụ —");
    expect(screen.getAllByTestId("provenance")[0]).toHaveTextContent("Nguồn dữ liệu: —");
  });
});

describe("telemetry client", () => {
  const point = (i: number) => ({ measured_at: new Date(1_700_000_000_000 + i * 5000).toISOString(), residual: i });

  it("sends limit=1000 and follows next_cursor", async () => {
    const urls: string[] = [];
    mockFetch((url) => {
      urls.push(url);
      const cursor = new URL(url, "http://x").searchParams.get("cursor");
      if (!cursor) return { body: { items: [point(0), point(1)], next_cursor: "c1" } };
      if (cursor === "c1") return { body: { items: [point(2)], next_cursor: "c2" } };
      return { body: { items: [point(3)], next_cursor: null } };
    });
    const result = await api.connectorTelemetry("connector-1", "run-1", "2026-10-21T00:00:00Z", "2026-10-21T01:00:00Z");
    expect(result.items.map((p) => p.residual)).toEqual([0, 1, 2, 3]);
    expect(result.truncated).toBe(false);
    expect(urls).toHaveLength(3);
    expect(urls.every((u) => new URL(u, "http://x").searchParams.get("limit") === "1000")).toBe(true);
    expect(new URL(urls[1], "http://x").searchParams.get("cursor")).toBe("c1");
  });

  it("stops at the page cap and reports truncation", async () => {
    const fetchMock = mockFetch(() => ({ body: { items: [point(0)], next_cursor: "more" } }));
    const result = await api.connectorTelemetry("connector-1", null, "2026-10-21T00:00:00Z", "2026-10-21T01:00:00Z");
    expect(fetchMock).toHaveBeenCalledTimes(MAX_TELEMETRY_PAGES);
    expect(result.truncated).toBe(true);
  });

  it("downsamples to at most 1500 points keeping first and last", () => {
    const data = Array.from({ length: 17_280 }, (_, i) => i);
    const out = downsample(data, 1500);
    expect(out).toHaveLength(1500);
    expect(out[0]).toBe(0);
    expect(out[out.length - 1]).toBe(17_279);
    expect(downsample([1, 2, 3], 1500)).toEqual([1, 2, 3]);
  });
});

describe("ACK selection", () => {
  it("uses the latest applied reduce_load of the current cycle", () => {
    const inc = incident({
      timeline: [
        { at: "2026-10-20T03:00:00Z", event: "opened" },
        { at: "2026-10-20T04:00:00Z", event: "recurrence" },
      ],
      commands: [
        { id: "a", type: "reduce_load", status: "applied", applied_at: "2026-10-20T03:10:00Z" },
        { id: "b", type: "reduce_load", status: "applied", applied_at: "2026-10-20T04:10:00Z" },
        { id: "c", type: "reduce_load", status: "applied", applied_at: "2026-10-20T04:20:00Z" },
        { id: "d", type: "reduce_load", status: "failed", applied_at: null },
      ],
    });
    expect(latestAppliedReduction(inc)?.id).toBe("c");
    const old = incident({
      commands: [{ id: "a", type: "reduce_load", status: "applied", applied_at: "2026-10-20T03:10:00Z" }],
      timeline: [{ at: "2026-10-20T05:00:00Z", event: "recurrence" }],
    });
    expect(latestAppliedReduction(old)).toBeUndefined();
  });
});

describe("command gates", () => {
  const operatorView = (over: Partial<Incident>) => {
    mockFetch((url) => {
      if (url.endsWith("/auth/me")) return me("operator");
      if (url.endsWith("/incidents/inc-1")) return { body: incident(over) };
      return { status: 404 };
    });
    renderAt("/incidents/inc-1", <IncidentDetail />, "/incidents/:id");
  };
  const verified = "2026-10-20T03:05:00Z";

  it("shows who requested each command (Operator / Datadog App)", async () => {
    operatorView({
      commands: [
        { id: "c1", type: "acknowledge", status: "applied", actor: "operator" },
        { id: "c2", type: "reduce_load", status: "applied", actor: "datadog-app" },
        { id: "c3", type: "maintenance", status: "failed" },
      ],
    });
    const cells = await screen.findAllByTestId("command-actor");
    expect(cells.map((c) => c.textContent)).toEqual(["Operator", "Datadog App", ""]);
  });

  it("unlocks maintenance while mitigating only for residual_not_recovered", () => {
    const check = (reason: Incident["reduction_check_reason"]) =>
      commandAllowed(
        incident({ state: "mitigating", reduction_check: "load_reduction_ineffective", reduction_check_reason: reason }),
        "maintenance",
      );
    expect(check("residual_not_recovered")).toBe(true);
    expect(check("load_not_reduced")).toBe(false);
    expect(check("insufficient_data")).toBe(false);
    expect(commandAllowed(incident({ state: "mitigating" }), "maintenance")).toBe(false);
    expect(commandAllowed(incident({ state: "mitigated" }), "maintenance")).toBe(true);
    expect(commandAllowed(incident({ state: "acknowledged" }), "maintenance")).toBe(false);
  });

  it("blocks verify_recovery until maintenance is applied", () => {
    expect(commandAllowed(incident({ state: "mitigated" }), "verify_recovery")).toBe(false);
    expect(commandAllowed(incident({ state: "mitigated", maintenance_applied_at: verified }), "verify_recovery")).toBe(true);
    expect(commandAllowed(incident({ state: "verifying", maintenance_applied_at: verified }), "verify_recovery")).toBe(false);
  });

  it("blocks reduce_load once the reduction is verified", () => {
    expect(commandAllowed(incident({ state: "mitigating" }), "reduce_load")).toBe(true);
    expect(commandAllowed(incident({ state: "acknowledged" }), "reduce_load")).toBe(true);
    expect(commandAllowed(incident({ state: "mitigating", load_reduction_verified_at: verified }), "reduce_load")).toBe(false);
    expect(commandAllowed(incident({ state: "open" }), "reduce_load")).toBe(false);
  });

  it("enables maintenance and shows the reason for residual_not_recovered", async () => {
    operatorView({
      state: "mitigating",
      load_reduction_verified_at: verified,
      reduction_check: "load_reduction_ineffective",
      reduction_check_reason: "residual_not_recovered",
    });
    const maintenance = await screen.findByRole("button", { name: "Bảo trì" });
    await waitFor(() => expect(maintenance).toBeEnabled());
    expect(screen.getByRole("button", { name: "Giảm tải" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Kiểm tra phục hồi" })).toBeDisabled();
    expect(screen.getByText(/Đã giảm dòng nhưng nhiệt chưa phục hồi sau 120 s/)).toBeInTheDocument();
  });

  it("keeps maintenance disabled and shows load_not_reduced in Vietnamese", async () => {
    operatorView({ state: "mitigating", reduction_check: "load_reduction_ineffective", reduction_check_reason: "load_not_reduced" });
    expect(await screen.findByText("Dòng chưa giảm")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Giảm tải" })).toBeEnabled());
    expect(screen.getByRole("button", { name: "Bảo trì" })).toBeDisabled();
    expect(
      reductionCheckText(incident({ reduction_check: "load_reduction_unverified", reduction_check_reason: "insufficient_data" })),
    ).toBe("Thiếu dữ liệu để xác nhận");
    expect(reductionCheckText(incident({ reduction_check: "load_reduction_unverified" }))).toBe("Thiếu dữ liệu để xác nhận");
    expect(reductionCheckText(incident())).toBeNull();
  });

  it("enables verify_recovery for an operator only after maintenance", async () => {
    operatorView({ state: "mitigated", maintenance_applied_at: "2026-10-20T03:20:00Z" });
    const verify = await screen.findByRole("button", { name: "Kiểm tra phục hồi" });
    await waitFor(() => expect(verify).toBeEnabled());
  });
});
