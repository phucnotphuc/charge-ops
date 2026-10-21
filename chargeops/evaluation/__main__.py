"""Offline evaluation (docs/EVALUATION_PROTOCOL.md).

python -m chargeops.evaluation tune                       # tuning seeds only; writes the locked config
python -m chargeops.evaluation run --protocol b1-eval-v3  # evaluation seeds; refuses without the lock
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from chargeops.evaluation import protocol, summary
from chargeops.evaluation.rules import ALERT_C, RECOVERED_C
from chargeops.evaluation.runner import detector_windows, record, run_arm, run_seed

LOCK = Path(__file__).parent / f"locked-{protocol.PROTOCOL_VERSION}.json"


def choose_threshold(healthy_maxima, grid, budget):
    """Lowest fixed threshold whose healthy runs alerting stays within the false-alert budget."""
    for threshold in grid:
        if sum(m is not None and m > threshold for m in healthy_maxima) <= budget:
            return threshold
    return None


def tune(lock_path):
    if lock_path.exists():
        existing = json.loads(lock_path.read_text(encoding="utf-8")).get("protocol_version")
        if existing == protocol.PROTOCOL_VERSION:
            raise SystemExit(
                f"refusing to overwrite the {existing} lock at {lock_path}; bump protocol_version"
            )
    config = {"a_threshold_c": float("inf"), "c_alert_c": ALERT_C}
    healthy_maxima, c_alerting, detail = [], 0, []
    for seed, kind, scenario in protocol.scenarios("tuning"):
        windows = detector_windows(run_arm(protocol.exogenous(seed, scenario), "B", config), config)
        means = [w["mean_temperature_c"] for w in windows if w["quality"]]
        peak = max(means, default=None)
        alerted = any(w["residual"] for w in windows)
        if kind == "healthy":
            healthy_maxima.append(peak)
            c_alerting += alerted
        detail.append(
            {"seed": seed, "scenario": scenario, "max_window_mean_c": peak, "residual_alert": alerted}
        )
    threshold = choose_threshold(healthy_maxima, protocol.A_GRID, protocol.FALSE_ALERT_BUDGET)
    if threshold is None:
        raise SystemExit("no fixed threshold in the grid meets the false-alert budget")
    lock = {
        "protocol_version": protocol.PROTOCOL_VERSION,
        "fingerprint": protocol.fingerprint(),
        "locked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "tuning_seeds": protocol.SEEDS["tuning"],
        "evaluation_seeds": protocol.SEEDS["evaluation"],
        "a_threshold_c": threshold,
        "a_rule": "lowest grid value with healthy tuning runs alerting <= false_alert_budget",
        "c_alert_c": ALERT_C,
        "c_recovered_c": RECOVERED_C,
        "c_rule": "product constants (CONTRACT §3); verified, not searched, on tuning seeds",
        "false_alert_budget": protocol.FALSE_ALERT_BUDGET,
        "c_tuning_healthy_runs_alerting": c_alerting,
        "c_within_budget": c_alerting <= protocol.FALSE_ALERT_BUDGET,
        "tuning": detail,
    }
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    print(
        f"locked A threshold {threshold} °C; C alert {ALERT_C} °C ({c_alerting} healthy tuning runs alerting)"
    )
    return lock


def load_lock(lock_path, version):
    """Refuse unless a lock exists for this exact protocol, fingerprint and disjoint seed split."""
    if not lock_path.exists():
        raise SystemExit(f"refusing to run: no locked config at {lock_path}; run `tune` first")
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    tuning = {s for seeds in lock.get("tuning_seeds", {}).values() for s in seeds}
    evaluation = {s for seeds in protocol.SEEDS["evaluation"].values() for s in seeds}
    if version != protocol.PROTOCOL_VERSION or lock.get("protocol_version") != version:
        raise SystemExit(f"refusing to run: protocol {version} does not match code/lock")
    if lock.get("fingerprint") != protocol.fingerprint():
        raise SystemExit("refusing to run: protocol definition changed after the config was locked")
    if lock.get("evaluation_seeds") != protocol.SEEDS["evaluation"] or tuning & evaluation:
        raise SystemExit("refusing to run: evaluation seeds differ from the lock or overlap tuning seeds")
    return lock


def run(lock_path, version, out):
    lock = load_lock(lock_path, version)
    config = {"a_threshold_c": lock["a_threshold_c"], "c_alert_c": lock["c_alert_c"]}
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    outcomes = []
    with (out / "runs.jsonl").open("w", encoding="utf-8") as f:
        for seed, _, scenario in protocol.scenarios("evaluation"):
            results = run_seed(protocol.exogenous(seed, scenario), config)
            records = {arm: record(result) for arm, result in results.items()}
            for rec in records.values():
                f.write(json.dumps(rec) + "\n")
            outcomes.append(summary.seed_outcome(records, detector_windows(results["B"], config)))
    runtime = time.perf_counter() - started
    result = summary.aggregate(outcomes, lock)
    result.update(runtime_s=runtime, runs=3 * len(outcomes), seeds=outcomes)
    (out / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(summary.markdown(result, runtime), encoding="utf-8")
    (out / "locked-config.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({result['runs']} runs, {runtime:.1f} s)")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline A/B/C evaluation on a simulated clock")
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("tune", help="fix thresholds on tuning seeds and write the locked config")
    t.add_argument("--lock", type=Path, default=LOCK)
    r = sub.add_parser("run", help="run 20 fault + 20 healthy evaluation seeds × A/B/C")
    r.add_argument(
        "--protocol", required=True, help=f"protocol version, currently {protocol.PROTOCOL_VERSION}"
    )
    r.add_argument("--lock", type=Path, default=LOCK)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    r.add_argument("--out", type=Path, default=Path("artifacts") / "evaluation" / stamp)
    args = parser.parse_args(argv)
    if args.command == "tune":
        return tune(args.lock)
    return run(args.lock, args.protocol, args.out)


if __name__ == "__main__":
    sys.exit(main() and 0)
