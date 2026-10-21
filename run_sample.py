"""
Monthly Event Sampler

Queries eligible devices from Redshift, then draws a stratified sample of
intrusion events from FiftyOne for the given month.

Usage:
    python run_sample.py --year 2026 --month 3
    python run_sample.py --year 2026 --month 3 --sample-size 1000 --dataset prod_intrusion_event_datalake_anonymized

Required .env variables (in addition to FiftyOne credentials):
    REDSHIFT_HOST, REDSHIFT_PORT (default 5439), REDSHIFT_DATABASE,
    REDSHIFT_USER, REDSHIFT_PASSWORD
"""

import argparse
import calendar
import datetime as dt
import json
import os
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import psycopg2

# Load credentials from .env BEFORE importing fiftyone
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key] = value.strip('"').strip("'")

import fiftyone as fo
from fiftyone import ViewField as F


OUTPUT_SAMPLES_DIR = Path(__file__).parent / "outputs" / "samples"
OUTPUT_PLOTS_DIR = Path(__file__).parent / "outputs" / "plots"


def get_devices_from_redshift():
    """Return the set of eligible canopy_device_ids (thing_name) from Redshift."""
    conn = psycopg2.connect(
        host=os.environ["REDSHIFT_HOST"],
        port=int(os.environ.get("REDSHIFT_PORT", 5439)),
        database=os.environ["REDSHIFT_DATABASE"],
        user=os.environ["REDSHIFT_USER"],
        password=os.environ["REDSHIFT_PASSWORD"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT thing_name
                FROM canopy_services_device
                WHERE serial_number ~ '^[0-9]{4}DSHPUN'
                  AND CAST(SUBSTRING(serial_number FROM 1 FOR 4) AS INT) >= 2508
                  AND serial_number NOT IN (
                      SELECT serial_number FROM internal_v1_1_serial_numbers
                  )
                  AND thing_name IS NOT NULL
                  AND thing_name != ''
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


def get_time_of_day(hour):
    if 0 <= hour < 6:
        return "Night"
    elif 6 <= hour < 12:
        return "Morning"
    elif 12 <= hour < 18:
        return "Afternoon"
    else:
        return "Evening"


def main():
    parser = argparse.ArgumentParser(description="Sample intrusion events for monthly FPR analysis")
    parser.add_argument("--year", type=int, required=True, help="Year (e.g. 2026)")
    parser.add_argument("--month", type=int, required=True, help="Month number (1–12)")
    parser.add_argument("--start-day", type=int, default=None, help="First day of range (default: last 28 days of month)")
    parser.add_argument("--end-day", type=int, default=None, help="Last day of range inclusive (default: last day of month)")
    parser.add_argument("--sample-size", type=int, default=1000, help="Target sample size (default: 1000)")
    parser.add_argument("--dataset", default="prod_intrusion_event_datalake_anonymized",
                        help="FiftyOne dataset name")
    parser.add_argument("--event-type", choices=["INTRUSION", "LEARNING"], default="INTRUSION",
                        help="Event type to sample (default: INTRUSION)")
    parser.add_argument(
        "--exclude-devices",
        nargs="+",
        default=[],
        metavar="DEVICE_ID",
        help="One or more canopy_device_id values to exclude from sampling.",
    )
    args = parser.parse_args()

    last_day_of_month = calendar.monthrange(args.year, args.month)[1]
    end_day = args.end_day if args.end_day is not None else last_day_of_month
    start_day = args.start_day if args.start_day is not None else max(1, end_day - 27)

    start_time = dt.datetime(args.year, args.month, start_day)
    end_time = dt.datetime(args.year, args.month, end_day) + dt.timedelta(days=1)  # exclusive upper bound
    last_day = end_day

    if start_day == 1 and end_day == last_day_of_month:
        month_label = f"{args.year}-{args.month:02d}"
    else:
        month_label = f"{args.year}-{args.month:02d}_{start_day:02d}-{end_day:02d}"

    # --- Devices from Redshift ---
    print("Querying Redshift for eligible devices...")
    include_devices = get_devices_from_redshift()
    print(f"Found {len(include_devices)} eligible devices")

    exclude_devices = set(args.exclude_devices)
    if exclude_devices:
        include_devices -= exclude_devices
        print(f"Excluded {len(exclude_devices)} device(s): {sorted(exclude_devices)}")
        print(f"{len(include_devices)} devices remaining after exclusions")

    # --- Events from FiftyOne ---
    print(f"Loading dataset '{args.dataset}'...")
    dataset = fo.load_dataset(args.dataset)

    event_type = args.event_type

    IEs = dataset.match(
        (F("capture_time") >= start_time) &
        (F("capture_time") < end_time) &
        (F("canopy_device_id").is_in(include_devices)) &
        (F("event_type") == event_type)
    )

    IE_devices = IEs.values("canopy_device_id")
    IE_times = IEs.values("capture_time")
    IE_events = IEs.values("event_id")
    IE_types = IEs.values("event_type")

    total_events_population = len(IE_events)
    num_devices_population = len(set(IE_devices))
    print(f"Found {total_events_population} {event_type.lower()} events from {num_devices_population} devices "
          f"({start_time.date()} – {dt.date(args.year, args.month, last_day)})")

    if total_events_population == 0:
        print(f"No {event_type.lower()} events found for this period. Exiting.")
        return

    target_total = args.sample_size

    df = pd.DataFrame({
        "event_id": IE_events,
        "device_id": IE_devices,
        "timestamp": pd.to_datetime(IE_times),
        "event_type": IE_types,
    })
    df["day"] = df["timestamp"].dt.date
    df["hour"] = df["timestamp"].dt.hour
    df["time_of_day"] = df["hour"].apply(get_time_of_day)

    group_keys = ["device_id", "day", "time_of_day"]

    # Step 1: Force at least 1 event per device
    device_grouped = df.groupby("device_id")
    forced_samples = [group.sample(n=1, random_state=42) for _, group in device_grouped]
    forced_df = pd.concat(forced_samples).reset_index(drop=True)
    forced_event_ids = set(forced_df["event_id"])
    print(f"Forced 1 event per device: {len(forced_df)} events from {len(forced_df)} devices")

    # Step 2: Proportional sampling for remaining quota
    remaining_target = target_total - len(forced_df)
    df_remaining = df[~df["event_id"].isin(forced_event_ids)]

    grouped = df_remaining.groupby(group_keys)
    group_sizes = grouped.size()
    group_sizes = group_sizes[group_sizes > 0]
    total_remaining = group_sizes.sum()

    raw_weights = (group_sizes / total_remaining) * remaining_target
    initial_plan = raw_weights.apply(np.floor).astype(int)

    shortfall = remaining_target - initial_plan.sum()
    if shortfall > 0:
        remainders = (raw_weights - initial_plan).sort_values(ascending=False)
        for key in remainders.head(shortfall).index:
            initial_plan[key] += 1

    sampled = []
    for key, n in initial_plan.items():
        if n <= 0:
            continue
        try:
            group = grouped.get_group(key)
            sampled.append(group.sample(n=n, random_state=42) if len(group) >= n else group)
        except KeyError:
            continue

    proportional_df = pd.concat(sampled).reset_index(drop=True) if sampled else pd.DataFrame()
    final_sample = pd.concat([forced_df, proportional_df]).drop_duplicates(subset="event_id").reset_index(drop=True)

    print(final_sample[["event_id", "device_id", "timestamp", "time_of_day"]].head())
    print(f"Sampled {len(final_sample)} {event_type} events from {final_sample['device_id'].nunique()} devices.")

    # --- Save sample JSON with metadata ---
    OUTPUT_SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_SAMPLES_DIR / f"{month_label}_{event_type.lower()}_sample.json"
    output_data = {
        "event_id": {"$in": sorted(final_sample["event_id"].tolist())},
        "metadata": {
            "month": month_label,
            "event_type": event_type,
            "start_date": str(start_time.date()),
            "end_date": str(dt.date(args.year, args.month, last_day)),
            "total_events_population": total_events_population,
            "num_devices_population": num_devices_population,
            "sample_size": len(final_sample),
            "dataset": args.dataset,
            "excluded_devices": sorted(exclude_devices) if exclude_devices else [],
        },
    }
    with open(output_file, "w") as f:
        json.dump(output_data, f, indent=2)
    print(f"Saved sample to {output_file}")

    # --- Visualization: Before vs After Sampling ---
    fig, axes = plt.subplots(2, 3, figsize=(15, 10))
    fig.suptitle(f"Distribution Before vs After Sampling ({month_label} – {event_type})", fontsize=14, fontweight='bold')

    time_order = ["Night", "Morning", "Afternoon", "Evening"]

    # Events per Device
    device_counts_before = df["device_id"].value_counts()
    axes[0, 0].hist(device_counts_before.values, bins=20, alpha=0.7,
                    label=f"Before (n={len(df)})", color='steelblue')
    axes[0, 0].set_xlabel("Events per Device")
    axes[0, 0].set_ylabel("Number of Devices")
    axes[0, 0].set_title("Events per Device (Before)")
    axes[0, 0].legend()

    device_counts_after = final_sample["device_id"].value_counts()
    axes[1, 0].hist(device_counts_after.values, bins=20, alpha=0.7,
                    label=f"After (n={len(final_sample)})", color='coral')
    axes[1, 0].set_xlabel("Events per Device")
    axes[1, 0].set_ylabel("Number of Devices")
    axes[1, 0].set_title("Events per Device (After)")
    axes[1, 0].legend()

    # Time of Day
    tod_before = df["time_of_day"].value_counts().reindex(time_order, fill_value=0)
    axes[0, 1].bar(tod_before.index, tod_before.values, alpha=0.7, color='steelblue')
    axes[0, 1].set_xlabel("Time of Day")
    axes[0, 1].set_ylabel("Event Count")
    axes[0, 1].set_title("Time of Day (Before)")
    for i, v in enumerate(tod_before.values):
        axes[0, 1].text(i, v + 10, f"{v/len(df)*100:.1f}%", ha='center', fontsize=9)

    tod_after = final_sample["time_of_day"].value_counts().reindex(time_order, fill_value=0)
    axes[1, 1].bar(tod_after.index, tod_after.values, alpha=0.7, color='coral')
    axes[1, 1].set_xlabel("Time of Day")
    axes[1, 1].set_ylabel("Event Count")
    axes[1, 1].set_title("Time of Day (After)")
    for i, v in enumerate(tod_after.values):
        axes[1, 1].text(i, v + 2, f"{v/len(final_sample)*100:.1f}%", ha='center', fontsize=9)

    # Events per Day
    day_counts_before = df.groupby("day").size()
    axes[0, 2].bar(range(len(day_counts_before)), day_counts_before.values, alpha=0.7, color='steelblue')
    axes[0, 2].set_xlabel("Day Index")
    axes[0, 2].set_ylabel("Event Count")
    axes[0, 2].set_title("Events per Day (Before)")
    axes[0, 2].set_xticks(range(0, len(day_counts_before), 5))

    day_counts_after = final_sample.groupby("day").size()
    axes[1, 2].bar(range(len(day_counts_after)), day_counts_after.values, alpha=0.7, color='coral')
    axes[1, 2].set_xlabel("Day Index")
    axes[1, 2].set_ylabel("Event Count")
    axes[1, 2].set_title("Events per Day (After)")
    axes[1, 2].set_xticks(range(0, len(day_counts_after), 5))

    plt.tight_layout()
    OUTPUT_PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    plot_file = OUTPUT_PLOTS_DIR / f"{month_label}_sampling_distributions.png"
    plt.savefig(plot_file, dpi=150)
    plt.show()
    print(f"Saved distribution plot to {plot_file}")


if __name__ == "__main__":
    main()
