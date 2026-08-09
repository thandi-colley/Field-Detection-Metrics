"""
Add a `serial_number` column to a classification CSV.

For each event_id in the CSV, looks up the device's serial_number from Redshift.

The event->device mapping is pulled from a UNION of two tables, because
`events_ended_and_classified_security_events` only contains events that produced
a security/CV classification (so a large fraction of LEARNING events are missing
from it). `events_security_events_image_evidence` fills in the rest:

    events_ended_and_classified_security_events (haven_event_id,  haven_device_id)
    events_security_events_image_evidence       (haven_event_id,  haven_device_id)

Then the device is translated to a serial_number via:

    canopy_services_user_device                 (havenid,          canopydeviceid)
    v_normalized_ids                            (thing_name,       serial_number)

Usage:
    python3 add_serial_numbers.py --csv csvs/event_classifications_2026_06_06.csv
    python3 add_serial_numbers.py --csv csvs/event_classifications_2026_06_06.csv --output csvs/with_serials.csv
"""

import argparse
import csv
import os
from pathlib import Path

import psycopg2

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key] = value.strip('"').strip("'")


CHUNK_SIZE = 1000


def load_serials_from_redshift(event_ids: list[str]) -> dict[str, str]:
    """Return {event_id: serial_number} for the given event_ids."""
    if not event_ids:
        return {}

    conn = psycopg2.connect(
        host=os.environ["REDSHIFT_HOST"],
        port=int(os.environ.get("REDSHIFT_PORT", 5439)),
        database=os.environ["REDSHIFT_DATABASE"],
        user=os.environ["REDSHIFT_USER"],
        password=os.environ["REDSHIFT_PASSWORD"],
    )
    result: dict[str, str] = {}
    try:
        with conn.cursor() as cur:
            for start in range(0, len(event_ids), CHUNK_SIZE):
                chunk = event_ids[start:start + CHUNK_SIZE]
                cur.execute(
                    """
                    SELECT DISTINCT
                        e.haven_event_id,
                        v.serial_number
                    FROM (
                        SELECT haven_event_id, haven_device_id
                        FROM events_ended_and_classified_security_events
                        WHERE haven_event_id = ANY(%s)
                        UNION
                        SELECT haven_event_id, haven_device_id
                        FROM events_security_events_image_evidence
                        WHERE haven_event_id = ANY(%s)
                    ) e
                    JOIN canopy_services_user_device d
                      ON e.haven_device_id = d.havenid
                    JOIN v_normalized_ids v
                      ON d.canopydeviceid = v.thing_name
                    """,
                    (chunk, chunk),
                )
                for eid, serial in cur.fetchall():
                    if eid and serial:
                        result[eid] = serial
    finally:
        conn.close()
    return result


def main():
    parser = argparse.ArgumentParser(description="Insert serial_number column into a classification CSV")
    parser.add_argument("--csv", required=True, help="Path to the input CSV")
    parser.add_argument("--output", help="Optional output path (defaults to overwriting the input CSV)")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = Path(__file__).parent / csv_path
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    output_path = Path(args.output) if args.output else csv_path
    if args.output and not output_path.is_absolute():
        output_path = Path(__file__).parent / output_path

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    if not rows:
        raise SystemExit("CSV is empty")

    event_ids = [r["event_id"].strip() for r in rows if r.get("event_id")]
    unique_ids = sorted(set(event_ids))
    print(f"Loaded {len(event_ids)} event_ids ({len(unique_ids)} unique) from {csv_path.name}")

    print("Querying Redshift for serial numbers...")
    event_to_serial = load_serials_from_redshift(unique_ids)
    print(f"Resolved {len(event_to_serial)} / {len(unique_ids)} events to a serial_number")

    if "serial_number" not in fieldnames:
        insert_at = fieldnames.index("event_id") + 1 if "event_id" in fieldnames else 0
        fieldnames.insert(insert_at, "serial_number")

    missing = 0
    for row in rows:
        eid = row.get("event_id", "").strip()
        serial = event_to_serial.get(eid, "")
        row["serial_number"] = serial
        if not serial:
            missing += 1

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {output_path}")
    print(f"  Rows with serial_number : {len(rows) - missing}")
    print(f"  Rows missing serial     : {missing}")


if __name__ == "__main__":
    main()
