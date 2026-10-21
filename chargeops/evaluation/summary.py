"""Per-seed outcomes and the A/B/C summary. Undefined values stay None, never 0."""

from collections import Counter
from statistics import median

from chargeops.evaluation import protocol
from chargeops.model import ORIGINAL_LIMIT_A, STEP_S

HOURS = protocol.DURATION_S / 3600


def stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0, "median": None, "min": None, "max": None}
    return {"n": len(values), "median": median(values), "min": min(values), "max": max(values)}


def duplicates(record):
    """Second and later non-rejected commands of one type within one incident handling cycle."""
    counts = Counter(
        (c["incident"], c["cycle"], c["type"]) for c in record["commands"] if c["status"] != "failed"
    )
    return sum(n - 1 for n in counts.values())


def detection(opened, onset, trip):
    """Match rule: first incident/alert at or after onset. Lead time only when the B control actually tripped."""
    before = opened is not None and (trip is None or opened < trip)
    return {
        "first_s": opened,
        "detected_before_trip": before,
        "latency_s": None if opened is None else opened - onset,
        "lead_time_s": trip - opened if before and trip is not None else None,
    }


def first_alert(windows, key, after=0):
    return next((w["end_s"] for w in windows if w[key] and w["end_s"] >= after), None)


def episodes(windows, key):
    return sum(
        1 for prev, w in zip([None, *windows], windows, strict=False) if w[key] and not (prev and prev[key])
    )


def seed_outcome(records, windows):
    """records: arm -> run record for one seed; windows: detector-only view of B's telemetry."""
    b = records["B"]
    fault = b["truth"]["fault"]
    onset = fault["onset_s"] if fault else None
    trip = b["trips_s"][0] if b["trips_s"] else None
    out = {
        "seed": b["seed"],
        "scenario": b["scenario"],
        "kind": "fault" if fault else "healthy",
        "b_trip_s": trip,
        "arms": {},
        "detectors": {},
    }
    for arm, rec in records.items():
        b_power, power = b["series"]["power_kw"], rec["series"]["power_kw"]
        limits = rec["series"]["current_limit_a"]
        row = {
            "incidents": len(rec["incidents"]),
            "trips": len(rec["trips_s"]),
            "interruption_min": rec["interruption_s"] / 60,
            "kwh": rec["kwh"],
            "kwh_delta_vs_b": rec["kwh"] - b["kwh"],
            "kwh_delta_while_derated": sum(
                p - q for p, q, lim in zip(power, b_power, limits, strict=True) if lim < ORIGINAL_LIMIT_A
            )
            * STEP_S
            / 3600,
            "duplicate_commands": duplicates(rec),
            "rejected_commands": sum(1 for c in rec["commands"] if c["status"] == "failed"),
            "commands": len(rec["commands"]),
        }
        if fault:
            matched = [i for i in rec["incidents"] if i["opened_s"] >= onset]
            first = matched[0] if matched else None
            row["pre_onset_incidents"] = len(rec["incidents"]) - len(matched)
            row.update(detection(first and first["opened_s"], onset, trip))
            for name, field in [
                ("effective_mitigation_s", "load_reduction_verified_s"),
                ("mitigated_s", "mitigated_s"),
                ("maintenance_s", "maintenance_applied_s"),
                ("resolved_s", "resolved_s"),
            ]:
                value = first and first[field]
                row[name] = None if value is None or rec["arm"] == "B" else value - first["opened_s"]
            row["final_state"] = first and first["final_state"]
        out["arms"][arm] = row
    for key in ["fixed", "residual"]:
        row = {"episodes": episodes(windows, key), "any_alert": any(w[key] for w in windows)}
        if fault:
            row["pre_onset_alert"] = any(w[key] for w in windows if w["end_s"] < onset)
            row.update(detection(first_alert(windows, key, onset), onset, trip))
        out["detectors"][key] = row
    return out


def aggregate(outcomes, lock):
    faults = [o for o in outcomes if o["kind"] == "fault"]
    healthy = [o for o in outcomes if o["kind"] == "healthy"]
    eligible = [o for o in faults if o["b_trip_s"] is not None]
    healthy_hours = len(healthy) * HOURS

    def per_hour(count):  # denominator: simulated healthy connector-hours
        return count / healthy_hours if healthy_hours else None

    def fault_block(rows):
        return {
            "recall_before_trip": sum(r["detected_before_trip"] for r in rows),
            "detected_any": sum(r["first_s"] is not None for r in rows),
            "detected_before_trip_among_b_tripped": sum(
                r["detected_before_trip"]
                for r, o in zip(rows, faults, strict=True)
                if o["b_trip_s"] is not None
            ),
            "latency_s": stats([r["latency_s"] for r in rows]),
            "lead_time_s": stats([r["lead_time_s"] for r in rows]),
            "pre_onset_false": sum(
                bool(r.get("pre_onset_incidents") or r.get("pre_onset_alert")) for r in rows
            ),
        }

    arms = {}
    for arm in ["A", "B", "C"]:
        f = [o["arms"][arm] for o in faults]
        h = [o["arms"][arm] for o in healthy]
        every = f + h
        arms[arm] = {
            "fault": {
                **fault_block(f),
                "effective_mitigation_s": stats([r["effective_mitigation_s"] for r in f]),
                "mitigated_s": stats([r["mitigated_s"] for r in f]),
                "maintenance_applied": sum(r["maintenance_s"] is not None for r in f),
                "resolved": sum(r["resolved_s"] is not None for r in f),
                "final_states": dict(Counter(str(r["final_state"]) for r in f)),
                "trips": sum(r["trips"] for r in f),
                "seeds_tripped": sum(r["trips"] > 0 for r in f),
                "interruption_min": sum(r["interruption_min"] for r in f),
                "kwh": sum(r["kwh"] for r in f),
                "kwh_delta_vs_b": sum(r["kwh_delta_vs_b"] for r in f),
                "kwh_delta_while_derated": sum(r["kwh_delta_while_derated"] for r in f),
            },
            "healthy": {
                "runs_with_incident": sum(r["incidents"] > 0 for r in h),
                "incidents": sum(r["incidents"] for r in h),
                "false_alerts_per_connector_hour": per_hour(sum(r["incidents"] for r in h)),
                "trips": sum(r["trips"] for r in h),
                "kwh": sum(r["kwh"] for r in h),
                "kwh_delta_vs_b": sum(r["kwh_delta_vs_b"] for r in h),
                "by_scenario": dict(
                    Counter(o["scenario"] for o in healthy if o["arms"][arm]["incidents"] > 0)
                ),
            },
            "duplicate_commands": sum(r["duplicate_commands"] for r in every),
            "rejected_commands": sum(r["rejected_commands"] for r in every),
            "commands": sum(r["commands"] for r in every),
        }
    detectors = {}
    for key in ["fixed", "residual"]:
        h = [o["detectors"][key] for o in healthy]
        detectors[key] = {
            **fault_block([o["detectors"][key] for o in faults]),
            "healthy_runs_alerting": sum(r["any_alert"] for r in h),
            "healthy_alert_episodes_per_connector_hour": per_hour(sum(r["episodes"] for r in h)),
            "healthy_by_scenario": dict(
                Counter(o["scenario"] for o in healthy if o["detectors"][key]["any_alert"])
            ),
        }
    c = arms["C"]
    return {
        "protocol_version": protocol.PROTOCOL_VERSION,
        "fingerprint": protocol.fingerprint(),
        "locked": {k: lock[k] for k in ["a_threshold_c", "c_alert_c", "c_recovered_c", "locked_at"]},
        "fault_seeds": len(faults),
        "healthy_seeds": len(healthy),
        "healthy_connector_hours": healthy_hours,
        "b_tripped_seeds": len(eligible),
        "arms": arms,
        "detectors": detectors,
        "targets": {
            "c_recall_before_trip_ge_18_of_20": c["fault"]["recall_before_trip"] >= 18,
            "c_healthy_runs_with_incident_le_1": c["healthy"]["runs_with_incident"] <= 1,
            "duplicate_commands_zero": all(a["duplicate_commands"] == 0 for a in arms.values()),
        },
    }


def fmt(value, digits=1):
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def span(s):
    if not s["n"]:
        return "n/a (n=0)"
    return f"{fmt(s['median'], 0)} [{fmt(s['min'], 0)}–{fmt(s['max'], 0)}] (n={s['n']})"


def markdown(summary, runtime_s):
    a, d = summary["arms"], summary["detectors"]
    nf, nh, ne = summary["fault_seeds"], summary["healthy_seeds"], summary["b_tripped_seeds"]
    lines = [
        f"# Evaluation summary — {summary['protocol_version']}",
        "",
        f"Fingerprint `{summary['fingerprint'][:16]}…`; locked A threshold {summary['locked']['a_threshold_c']} °C, "
        f"C alert {summary['locked']['c_alert_c']} °C / recovery {summary['locked']['c_recovered_c']} °C "
        f"(locked {summary['locked']['locked_at']}). Simulated clock; runtime {runtime_s:.1f} s wall. "
        f"{nf} fault + {nh} healthy seeds × A/B/C. B tripped on {ne}/{nf} fault seeds. "
        "Times: median [min–max] seconds of simulated clock.",
        "",
        "## Operational (A/B/C runs)",
        "",
        "| Metric | A fixed + workflow | B residual, no action | C ChargeOps |",
        "|---|---|---|---|",
    ]

    def row(label, get):
        lines.append(f"| {label} | " + " | ".join(get(a[x]) for x in ["A", "B", "C"]) + " |")

    row(f"Fault detected before B trip (/{nf})", lambda x: fmt(x["fault"]["recall_before_trip"]))
    row(f"… among B-tripped seeds (/{ne})", lambda x: fmt(x["fault"]["detected_before_trip_among_b_tripped"]))
    row("Detection latency from onset", lambda x: span(x["fault"]["latency_s"]))
    row(f"Lead time vs B trip (eligible {ne})", lambda x: span(x["fault"]["lead_time_s"]))
    row("Alert → effective load reduction", lambda x: span(x["fault"]["effective_mitigation_s"]))
    row("Alert → mitigated (residual < 3 °C)", lambda x: span(x["fault"]["mitigated_s"]))
    row(
        f"Maintenance applied / resolved (/{nf})",
        lambda x: f"{x['fault']['maintenance_applied']} / {x['fault']['resolved']}",
    )
    row(
        "Final incident states (fault)",
        lambda x: ", ".join(f"{k}: {v}" for k, v in sorted(x["fault"]["final_states"].items())),
    )
    row(
        "Trips on fault seeds (seeds tripped)",
        lambda x: f"{x['fault']['trips']} ({x['fault']['seeds_tripped']})",
    )
    row("Interruption minutes (fault)", lambda x: fmt(x["fault"]["interruption_min"]))
    row("kWh served (fault)", lambda x: fmt(x["fault"]["kwh"]))
    row("Signed kWh vs B (fault)", lambda x: fmt(x["fault"]["kwh_delta_vs_b"], 2))
    row("… of which while derated", lambda x: fmt(x["fault"]["kwh_delta_while_derated"], 2))
    row("Pre-onset incidents (fault seeds)", lambda x: fmt(x["fault"]["pre_onset_false"]))
    row(f"Healthy runs with incident (/{nh})", lambda x: fmt(x["healthy"]["runs_with_incident"]))
    row("False alerts / connector-hour", lambda x: fmt(x["healthy"]["false_alerts_per_connector_hour"], 2))
    row(
        "Healthy false incidents by scenario",
        lambda x: ", ".join(f"{k}: {v}" for k, v in sorted(x["healthy"]["by_scenario"].items())) or "none",
    )
    row("Trips on healthy seeds", lambda x: fmt(x["healthy"]["trips"]))
    row("Signed kWh vs B (healthy)", lambda x: fmt(x["healthy"]["kwh_delta_vs_b"], 2))
    row(
        "Commands / rejected / duplicate",
        lambda x: f"{x['commands']} / {x['rejected_commands']} / {x['duplicate_commands']}",
    )
    lines += [
        "",
        "## Detector only (both detectors on B's un-intervened telemetry)",
        "",
        "| Metric | Fixed threshold | Load-aware residual |",
        "|---|---|---|",
    ]

    def drow(label, get):
        lines.append(f"| {label} | {get(d['fixed'])} | {get(d['residual'])} |")

    drow(f"Detected before B trip (/{nf})", lambda x: fmt(x["recall_before_trip"]))
    drow(f"… among B-tripped seeds (/{ne})", lambda x: fmt(x["detected_before_trip_among_b_tripped"]))
    drow(f"Detected at all after onset (/{nf})", lambda x: fmt(x["detected_any"]))
    drow("Latency from onset", lambda x: span(x["latency_s"]))
    drow("Lead time vs B trip", lambda x: span(x["lead_time_s"]))
    drow("Fault seeds alerting before onset", lambda x: fmt(x["pre_onset_false"]))
    drow(f"Healthy runs alerting (/{nh})", lambda x: fmt(x["healthy_runs_alerting"]))
    drow(
        "Healthy alert episodes / connector-hour",
        lambda x: fmt(x["healthy_alert_episodes_per_connector_hour"], 2),
    )
    drow(
        "Healthy alerting by scenario",
        lambda x: ", ".join(f"{k}: {v}" for k, v in sorted(x["healthy_by_scenario"].items())) or "none",
    )
    t = summary["targets"]
    lines += [
        "",
        "## Internal targets (PLAN)",
        "",
        f"- C detects ≥18/20 faults before B trip: **{'met' if t['c_recall_before_trip_ge_18_of_20'] else 'missed'}**",
        f"- C ≤1 false incident in 20 healthy runs: **{'met' if t['c_healthy_runs_with_incident_le_1'] else 'missed'}**",
        f"- No duplicate commands: **{'met' if t['duplicate_commands_zero'] else 'missed'}**",
        "",
        "Simulation only: thresholds, trip temperature and operator timings are simulated assumptions, "
        "not equipment safety limits; no extrapolation to real stations.",
        "",
    ]
    return "\n".join(lines)
