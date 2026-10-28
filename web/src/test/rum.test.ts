import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@datadog/browser-rum", () => ({
  datadogRum: { init: vi.fn(), addAction: vi.fn(), addTiming: vi.fn() },
}));

const FULL_ENV = {
  VITE_DD_RUM_APPLICATION_ID: "app-id",
  VITE_DD_RUM_CLIENT_TOKEN: "pub-token",
  VITE_DD_SITE: "ap1.datadoghq.com",
  VITE_DD_ENV: "hackathon",
  VITE_DD_VERSION: "0.1.0",
};

async function load() {
  vi.resetModules();
  const sdk = (await import("@datadog/browser-rum")).datadogRum;
  const rum = await import("../rum");
  return { sdk, rum };
}

describe("rum", () => {
  beforeEach(() => vi.clearAllMocks());

  it("stays off without env values", async () => {
    const { sdk, rum } = await load();
    expect(rum.initRum({})).toBe(false);
    expect(rum.initRum({ ...FULL_ENV, VITE_DD_RUM_CLIENT_TOKEN: "" })).toBe(false);
    expect(rum.initRum({ ...FULL_ENV, VITE_DD_RUM_APPLICATION_ID: "" })).toBe(false);
    expect(sdk.init).not.toHaveBeenCalled();
  });

  it("initialises once with public config and APM linking", async () => {
    const { sdk, rum } = await load();
    expect(rum.initRum(FULL_ENV)).toBe(true);
    expect(rum.initRum(FULL_ENV)).toBe(true);
    expect(sdk.init).toHaveBeenCalledTimes(1);
    const cfg = vi.mocked(sdk.init).mock.calls[0][0] as unknown as Record<string, unknown>;
    expect(cfg).toMatchObject({
      applicationId: "app-id",
      clientToken: "pub-token",
      site: "ap1.datadoghq.com",
      service: "chargeops-web",
      env: "hackathon",
      version: "0.1.0",
      sessionSampleRate: 100,
      sessionReplaySampleRate: 0,
      trackUserInteractions: true,
      trackResources: true,
      defaultPrivacyLevel: "mask-user-input",
    });
    const urls = cfg.allowedTracingUrls as { match: string }[];
    expect(urls.some((u) => u.match === `${window.location.origin}/api/`)).toBe(true);
  });

  it("records command actions and seen-to-command timing", async () => {
    const { sdk, rum } = await load();
    rum.initRum(FULL_ENV);
    rum.trackCommand("acknowledge", { incident_id: "i1" });
    expect(sdk.addAction).toHaveBeenCalledWith("command_sent", { type: "acknowledge", incident_id: "i1" });
    expect(sdk.addTiming).not.toHaveBeenCalled();
    rum.markIncidentSeen("i1");
    rum.markIncidentSeen("i1");
    rum.trackCommand("reduce_load", { incident_id: "i1" });
    expect(sdk.addTiming).not.toHaveBeenCalled();
    expect(sdk.addAction).toHaveBeenLastCalledWith(
      "command_sent",
      expect.objectContaining({ type: "reduce_load", incident_seen_to_command_ms: expect.any(Number) }),
    );
  });

  it("is a no-op when RUM is off", async () => {
    const { sdk, rum } = await load();
    rum.initRum({});
    rum.markIncidentSeen("i1");
    rum.trackCommand("acknowledge", {});
    expect(sdk.addAction).not.toHaveBeenCalled();
    expect(sdk.addTiming).not.toHaveBeenCalled();
  });
});
