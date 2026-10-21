import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../api/auth";
import { DemoControl } from "../pages/DemoControl";
import { Evaluations } from "../pages/Evaluations";
import { IncidentDetail } from "../pages/IncidentDetail";
import { Overview } from "../pages/Overview";
import { connector, incident } from "./fixtures";

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
              connector({ connector_id: "fresh", freshness_seconds: 5 }),
              connector({ connector_id: "old", freshness_seconds: 45 }),
              connector({ connector_id: "none", freshness_seconds: null, latest: null }),
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
            items: [{ id: "run-abcdef12", status: "active", mode: "datadog", scenario: "thermal_fault", seed: 7, datadog_budget: { used: 3, limit: 20 } }],
            next_cursor: null,
          },
        };
      if (url.endsWith("/connectors")) return { body: { items: [connector()], next_cursor: null } };
      return { status: 404 };
    });
    renderAt("/demo", <DemoControl />);
    expect(await screen.findByTestId("budget")).toHaveTextContent("3/20");
    expect(screen.getByRole("button", { name: "Tạo run" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Dừng" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Chèn lỗi" })).toBeDisabled();
  });

  it("treats a missing /evaluations endpoint as an empty state", async () => {
    mockFetch((url) => (url.endsWith("/auth/me") ? unauthenticated : { status: 404, body: { error: { code: "not_found", message: "x", request_id: "r" } } }));
    renderAt("/evaluations", <Evaluations />);
    expect(await screen.findByTestId("empty")).toHaveTextContent("Chưa có kết quả đánh giá");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
