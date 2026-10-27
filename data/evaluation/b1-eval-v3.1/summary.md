# Evaluation summary — b1-eval-v3.1

Fingerprint `f54276de8d5584c8…`; locked A threshold 74.0 °C, C alert 8.0 °C / recovery 3.0 °C (locked 2026-10-21T08:21:51.979853Z). Simulated clock; runtime 2.4 s wall. 20 fault + 20 healthy seeds × A/B/C. B tripped on 7/20 fault seeds. Times: median [min–max] seconds of simulated clock.

## Operational (A/B/C runs)

| Metric | A fixed + workflow | B residual, no action | C ChargeOps |
|---|---|---|---|
| Fault detected before B trip (/20) | 8 | 20 | 20 |
| … among B-tripped seeds (/7) | 5 | 7 | 7 |
| Detection latency from onset | 340 [230–740] (n=9) | 155 [100–455] (n=20) | 155 [100–455] (n=20) |
| Lead time vs B trip (eligible 7) | 435 [230–600] (n=5) | 575 [40–885] (n=7) | 575 [40–885] (n=7) |
| Alert → effective load reduction | 135 [135–135] (n=9) | n/a (n=0) | 135 [135–405] (n=20) |
| Alert → mitigated (residual < 3 °C) | 735 [135–750] (n=7) | n/a (n=0) | 705 [660–750] (n=19) |
| Maintenance applied / resolved (/20) | 9 / 1 | 0 / 0 | 19 / 13 |
| Final incident states (fault) | mitigated: 1, mitigating: 2, resolved: 1, undetected: 11, verifying: 5 | open: 20 | mitigating: 1, resolved: 13, verifying: 6 |
| Trips on fault seeds (seeds tripped) | 0 (0) | 14 (7) | 1 (1) |
| Interruption minutes (fault) | 0.0 | 59.8 | 4.9 |
| kWh served (fault) | 355.4 | 349.5 | 327.0 |
| Signed kWh vs B (fault) | 5.87 | 0.00 | -22.54 |
| … of which while derated | -8.68 | 0.00 | -47.25 |
| Pre-onset incidents (fault seeds) | 2 | 0 | 0 |
| Healthy runs with incident (/20) | 5 | 0 | 0 |
| False alerts / connector-hour | 0.90 | 0.00 | 0.00 |
| Healthy false incidents by scenario | demand_drop: 1, healthy: 2, load_increase: 2 | none | none |
| Trips on healthy seeds | 0 | 0 | 0 |
| Signed kWh vs B (healthy) | -40.57 | 0.00 | 0.00 |
| Commands / rejected / duplicate | 78 / 0 / 0 | 0 / 0 / 0 | 78 / 0 / 0 |

## Detector only (both detectors on B's un-intervened telemetry)

| Metric | Fixed threshold | Load-aware residual |
|---|---|---|
| Detected before B trip (/20) | 8 | 20 |
| … among B-tripped seeds (/7) | 5 | 7 |
| Detected at all after onset (/20) | 10 | 20 |
| Latency from onset | 415 [230–740] (n=10) | 155 [100–455] (n=20) |
| Lead time vs B trip | 435 [230–600] (n=5) | 575 [40–885] (n=7) |
| Fault seeds alerting before onset | 2 | 0 |
| Healthy runs alerting (/20) | 5 | 0 |
| Healthy alert episodes / connector-hour | 0.50 | 0.00 |
| Healthy alerting by scenario | demand_drop: 1, healthy: 2, load_increase: 2 | none |

## Internal targets (PLAN)

- C detects ≥18/20 faults before B trip: **met**
- C ≤1 false incident in 20 healthy runs: **met**
- No duplicate commands: **met** — holds by construction of the scripted operator (one command per type and handling cycle); it is not evidence of product idempotency or deduplication.

Simulation only: thresholds, trip temperature and operator timings are simulated assumptions, not equipment safety limits; no extrapolation to real stations.
