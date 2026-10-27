import { render, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { StationDisplay } from "../pages/StationDisplay";
import { StationOperator } from "../pages/StationOperator";
import { fmtEta } from "../lib/format";

const STATION = {
  station_id: "station-2",
  connectors: [
    { connector_id: "connector-4", status: "limited", soc_percent: 47.3, power_kw: 12.1, current_a: 30, connector_temperature_c: 52.4, energy_kwh: 9.8, minutes_to_full: 140, measured_at: "2026-10-25T03:00:00Z" },
    { connector_id: "connector-5", status: "charging", soc_percent: 63, power_kw: 48, current_a: 120, connector_temperature_c: 41, energy_kwh: 15.2, minutes_to_full: 28, measured_at: "2026-10-25T03:00:00Z" },
    { connector_id: "connector-6", status: "idle", soc_percent: null, power_kw: 0, current_a: 0, connector_temperature_c: 30, energy_kwh: null, minutes_to_full: null, measured_at: "2026-10-25T03:00:00Z" },
  ],
};

function mock(routes: Record<string, unknown>) {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const key = Object.keys(routes).find((k) => url.includes(k));
    return key
      ? new Response(JSON.stringify(routes[key]), { status: 200, headers: { "Content-Type": "application/json" } })
      : new Response(JSON.stringify({ error: { code: "not_found", message: "x", request_id: null } }), { status: 404 });
  }));
}

afterEach(() => vi.unstubAllGlobals());

const at = (path: string, el: ReactElement, pattern: string) =>
  render(<MemoryRouter initialEntries={[path]}><Routes><Route path={pattern} element={el} /></Routes></MemoryRouter>);

test("driver view shows battery, power, temperature and friendly status only", async () => {
  mock({ "/stations/station-2": STATION });
  at("/stations/station-2", <StationDisplay />, "/stations/:stationId");
  const card = await screen.findByTestId("card-connector-4");
  expect(within(card).getByText("Đang sạc chậm để bảo vệ đầu sạc")).toBeInTheDocument();
  expect(within(card).getByText("47%")).toBeInTheDocument();
  expect(within(card).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "47.3");
  expect(within(card).getByText(/52[.,]4 °C/)).toBeInTheDocument();
  expect(within(screen.getByTestId("card-connector-6")).getByText("Trống")).toBeInTheDocument();
  const page = document.body.textContent ?? "";
  for (const word of ["Residual", "residual", "Sự cố", "incident", "Idempotency"]) expect(page).not.toContain(word);
});

test("operator view adds residual and incident link", async () => {
  mock({
    "/stations/station-2": STATION,
    "/insights/fleet": { run_id: "r1", windows: [{ connector_id: "connector-4", end: "2026-10-25T03:00:00Z", quality: true, residual_mean: 9.1 }] },
    "/insights/incidents": { total: 1, by_state: [{ state: "mitigating", count: 1 }], by_connector: [], mtta_s: 30, mttm_s: null, mttr_s: null },
    "/connectors/connector-4/trend": { run_id: "r1", connector_id: "connector-4", points: [] },
    "/connectors": { items: [{ connector_id: "connector-4", station_id: "station-2", charger_id: "charger-2", run_id: "r1", run_status: "active", mode: "local", measured_at: "2026-10-25T03:00:00Z", freshness_s: 3, stale: false, active_incident: { id: "i1", state: "mitigating" }, latest: null }], next_cursor: null },
  });
  at("/stations/station-2/operator", <StationOperator />, "/stations/:stationId/operator");
  const card = await screen.findByTestId("card-connector-4");
  expect(await within(card).findByText(/9[.,]1 °C/)).toBeInTheDocument();
  expect(within(card).getByRole("link", { name: /đang giảm tải/i })).toHaveAttribute("href", "/incidents/i1");
});

test("eta formatting", () => {
  expect(fmtEta(28)).toBe("khoảng 28 phút");
  expect(fmtEta(140)).toBe("khoảng 2 giờ 20 phút");
  expect(fmtEta(0)).toBe("Đã đầy");
  expect(fmtEta(null)).toBe("—");
});
