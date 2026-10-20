"""Reproducible audit of the Autosun–PolyU v3 dataset (docs/DATA_CARD.md, "Kiểm định").

Run from the repo root (raw/processed data stay git-ignored; only the manifest is committed):
    uv run --with pandas==2.2.3 --with openpyxl==3.1.5 python -I scripts/audit_autosun.py
"""

import csv
import hashlib
import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook

RAW = Path("data/raw/autosun-v3")
CSV = Path("data/processed/autosun_v3_longer_than_30.csv")
MANIFEST = Path("data/manifests/autosun-v3.json")
BASE = "https://data.mendeley.com/public-files/datasets/c7gg94tmvz/files"
FILES = {  # published SHA-256 from the Mendeley public API (version 3)
    "Readme.txt": (
        "77179ce8-2b20-465e-888b-901e8acbfc55",
        "0a535074de55e4d10592266fbbce588132d37f60ba5192b6b6812e0b128115b2",
    ),
    "processed_data_longer_than_30.xlsx": (
        "0bd6856e-41e5-45a4-ab75-e9f1273dff85",
        "8d7c676bbf2042a4cc8124d12aa5652b6a335bc43e3d4d984e819a0078e08c70",
    ),
}
SEED, SAMPLE = 20261020, 50


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch():
    RAW.mkdir(parents=True, exist_ok=True)
    files = []
    for name, (file_id, published) in FILES.items():
        path = RAW / name
        if not path.exists():
            urllib.request.urlretrieve(f"{BASE}/{file_id}/file_downloaded", path)
        actual = sha256(path)
        assert actual == published, f"{name}: SHA-256 {actual} != published {published}"
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")
        files.append(
            {"name": name, "bytes": path.stat().st_size, "sha256": actual, "downloaded_at": modified}
        )
    return files


def to_csv():
    """Stream all sheets into one CSV with a `sheet` column; values unchanged."""
    counts = {}
    if CSV.exists():
        return counts
    CSV.parent.mkdir(parents=True, exist_ok=True)
    workbook = load_workbook(RAW / "processed_data_longer_than_30.xlsx", read_only=True)
    with CSV.open("w", newline="", encoding="utf-8") as f:
        out, first = csv.writer(f), None
        for sheet in workbook.worksheets:
            rows = sheet.iter_rows(values_only=True)
            header = next(rows)
            if first is None:
                first = header
                out.writerow(["sheet", *header])
            assert header == first, (sheet.title, header)
            counts[sheet.title] = 0
            for row in rows:
                if any(v is not None for v in row):
                    out.writerow([sheet.title, *row])
                    counts[sheet.title] += 1
    return counts


def thermal_fit(data, column):
    """First-order lag per 60 s step, dT = a*(ambient_s + k*I^2 - T_prev), session fixed effects."""
    frame = data.assign(T_prev=data.groupby("transaction_id")[column].shift(), i2=data["charginga"] ** 2)
    frame["dT"] = frame[column] - frame["T_prev"]
    frame = frame.dropna(subset=["T_prev"])
    by = frame.groupby("transaction_id")
    x = frame[["i2", "T_prev"]] - by[["i2", "T_prev"]].transform("mean")
    y = frame["dT"] - by["dT"].transform("mean")
    (b1, b2), *_ = np.linalg.lstsq(x.values, y.values, rcond=None)
    r2 = 1 - float(((y - x.values @ np.array([b1, b2])) ** 2).sum() / (y**2).sum())
    return {"k_c_per_a2": round(b1 / -b2, 6), "tau_min": round(-1 / np.log(1 + b2), 1), "r2_dT": round(r2, 3)}


def main():
    files = fetch()
    sheet_rows = to_csv()
    raw = pd.read_csv(CSV, dtype={"transaction_id": str}, parse_dates=["begin_time", "end_time"])
    columns = [c for c in raw.columns if c != "sheet"]
    data = raw.drop_duplicates(subset=columns).sort_values(["transaction_id", "end_time"])
    by = data.groupby("transaction_id")
    step = by["end_time"].diff().dt.total_seconds()
    power_error = (data["out_power"] - data["chargingv"] * data["charginga"] / 1000).abs() / data[
        "out_power"
    ].where(data["out_power"] > 1)
    findings = {
        "rows_raw": len(raw),
        "rows_per_sheet": sheet_rows or raw["sheet"].value_counts().sort_index().to_dict(),
        "readme_claims": {"sessions": 21175, "sampling_points": 1547432, "period": "2020-10 to 2023-10"},
        "cross_sheet_duplicate_rows": int(raw.duplicated(subset=columns).sum()),
        "rows_after_dedup": len(data),
        "sessions_after_dedup": int(data["transaction_id"].nunique()),
        "ids_reused_for_different_rows": int(data.loc[data["id"].duplicated(keep=False), "id"].nunique()),
        "duplicate_transaction_end_time": int(data.duplicated(subset=["transaction_id", "end_time"]).sum()),
        "missing_values": int(raw.isna().sum().sum()),
        "share_steps_60s": round(float((step.dropna().round() == 60).mean()), 4),
        "sessions_with_gap_over_61s": int((step > 61).groupby(data["transaction_id"]).any().sum()),
        "power_vs_v_times_a_rel_error_p99": round(float(power_error.quantile(0.99)), 4),
        "gun_temperature_range_c": [
            int(data[["charging_gun_temperature1", "charging_gun_temperature2"]].min().min()),
            int(data[["charging_gun_temperature1", "charging_gun_temperature2"]].max().max()),
        ],
        "end_time_range": [str(data["end_time"].min()), str(data["end_time"].max())],
        "faulty_battery_sessions": int((by["label"].max() == 1).sum()),
    }
    sessions = by.agg(
        rows=("id", "size"),
        label=("label", "max"),
        a_min=("charginga", "min"),
        a_max=("charginga", "max"),
        a_mean=("charginga", "mean"),
        v_min=("chargingv", "min"),
        v_max=("chargingv", "max"),
        t_min=("charging_gun_temperature1", "min"),
        t_max=("charging_gun_temperature1", "max"),
        t2_min=("charging_gun_temperature2", "min"),
        t2_max=("charging_gun_temperature2", "max"),
    )
    gap = (step > 61).groupby(data["transaction_id"]).any()
    rules = [
        ("label == 0 (no battery fault)", sessions["label"] == 0),
        ("no gap > 61 s", ~gap),
        (">= 30 rows", sessions["rows"] >= 30),
        ("0 < current <= 250 A every row", (sessions["a_min"] > 0) & (sessions["a_max"] <= 250)),
        ("200 <= voltage <= 750 V", (sessions["v_min"] >= 200) & (sessions["v_max"] <= 750)),
        (
            "gun temperatures within 0..120 C",
            (sessions[["t_min", "t2_min"]].min(axis=1) >= 0)
            & (sessions[["t_max", "t2_max"]].max(axis=1) <= 120),
        ),
    ]
    keep, kept = pd.Series(True, index=sessions.index), []
    for name, ok in rules:
        keep &= ok
        kept.append({"rule": name, "sessions_remaining": int(keep.sum())})
    eligible = sessions[keep]
    chosen = eligible.sample(n=SAMPLE, random_state=SEED).sort_index()
    clean = data[data["transaction_id"].isin(eligible.index)]
    manifest = {
        "id": "autosun-polyu-v3",
        "title": "A dataset of EV battery charging from Autosun and Hong Kong Polytechnic University",
        "doi": "10.17632/c7gg94tmvz.3",
        "url": "https://data.mendeley.com/datasets/c7gg94tmvz/3",
        "license": "CC BY 4.0",
        "authors": ["haosen yang", "Jinpeng Tian", "Weijie Mai", "Chi-yung Chung"],
        "files": files,
        "units_verified": {
            "chargingv": "V",
            "charginga": "A",
            "out_power": "kW (= V*A/1000)",
            "gun temperatures": "C (assumed)",
        },
        "timezone": "not stated by source; timestamps kept as published (likely China local time, UTC+8)",
        "findings": findings,
        "selection": {
            "dedup_key": "(transaction_id, end_time) after dropping cross-sheet full-row duplicates; `id` is not unique",
            "rules": kept,
            "eligible_rows": int(eligible["rows"].sum()),
            "seed": SEED,
            "sample_size": SAMPLE,
            "selected_transaction_ids": list(chosen.index),
        },
        "thermal_fit_eligible_sessions": {
            "gun_temperature1": thermal_fit(clean, "charging_gun_temperature1"),
            "gun_temperature2": thermal_fit(clean, "charging_gun_temperature2"),
            "note": "1 C sensor resolution, no ambient; order-of-magnitude calibration only",
        },
        "use": "load profiles only (charginga/chargingv); battery `label` is not charger ground truth",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("findings", "thermal_fit_eligible_sessions")}, indent=2))
    print(f"eligible sessions {len(eligible)}; manifest written to {MANIFEST}")


if __name__ == "__main__":
    main()
