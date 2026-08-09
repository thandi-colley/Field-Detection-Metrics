"""
Import annotated events from classification CSVs into the FieldEvents01 FiftyOne dataset.

For each event_id in the input CSVs, the script:
    1. Looks up the sample in prod_intrusion_event_datalake_anonymized
    2. Copies it into FieldEvents01 (skipping event_ids already present)
    3. Attaches all annotation fields from the CSV

Usage:
        python3 import_annotations_to_fieldevents01.py --csv csvs/event_classifications_2026_04_13.csv
        python3 import_annotations_to_fieldevents01.py --csv csvs/event_classifications_2026_02_09.csv --csv csvs/event_classifications_2026_03_08.csv --csv csvs/event_classifications_2026_04_13.csv --update-existing
"""

import argparse
import csv
import os
import time
from pathlib import Path

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

# ── Configuration ──────────────────────────────────────────────────────────────
SOURCE_DATASET = "prod_intrusion_event_datalake_anonymized"
TARGET_DATASET = "FieldEvents01"

# When connected through the FiftyOne Teams API proxy each `set_values()` call
# is a round-trip through an HTTP proxy. The overhead per call dominates, so
# the default chunk size is set to a large value to minimise total round-trips
# to one per field (N_fields calls total). Tune via `--chunk-size`.
DEFAULT_CHUNK_SIZE = 1000

# Annotation fields to copy from CSV onto each sample
ANNOTATION_FIELDS = [
    "person_in_image",
    "person_visible_in_zone",
    "person_intruding",
    "person_in_bbox",
    "discard_criteria",
    "environment",
    "non_person_movement",
    "out_of_snapshot_activity",
    "body_position",
    "interaction_type",
    "body_part_in_bed",
    "intruding_from",
    "person_obstructed_by",
    "fp_cause",
    "fn_cause",
    "major_class",
    "intermediary_class",
]


def parse_csv_value(value: str, field_type=None, field_name=None):
    """Cast a CSV string to the appropriate Python type based on the existing field schema."""
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    type_name = type(field_type).__name__ if field_type is not None else ""

    # Fields that should always be stored as lists
    LIST_FIELDS = {"non_person_movement", "discard_criteria", "out_of_snapshot_activity"}
    if field_name in LIST_FIELDS or type_name == "ListField":
        if stripped == "":
            return []
        sep = ";" if ";" in stripped else ","
        return [item.strip() for item in stripped.split(sep) if item.strip()]

    if type_name == "BooleanField":
        if stripped == "":
            return None
        return stripped.lower() == "true"
    # StringField or unknown
    return None if stripped == "" else stripped


def load_annotations(csv_files: list[Path]) -> dict[str, dict]:
    """Read all CSV files and return {event_id: annotation_dict}."""
    annotations: dict[str, dict] = {}
    for path in csv_files:
        if not path.exists():
            print(f"WARNING: CSV not found, skipping: {path}")
            continue
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                eid = row["event_id"].strip()
                if eid in annotations:
                    print(f"WARNING: duplicate event_id across CSVs (keeping first): {eid}")
                    continue
                # Store the source CSV filename for traceability
                row["_source_csv"] = path.name
                annotations[eid] = row
        print(f"  Loaded {path.name}")
    return annotations


def resolve_csv_paths(csv_args: list[str]) -> list[Path]:
    """Resolve user-provided CSV paths relative to the workspace when needed."""
    base_dir = Path(__file__).parent
    resolved = []
    for csv_arg in csv_args:
        path = Path(csv_arg)
        if not path.is_absolute():
            path = base_dir / path
        resolved.append(path)
    return resolved


def main():
    parser = argparse.ArgumentParser(description="Import CSV annotations into FieldEvents01")
    parser.add_argument(
        "--csv",
        dest="csv_files",
        action="append",
        required=True,
        help="Path to a classification CSV. Repeat --csv to provide multiple files.",
    )
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="Re-write annotation fields on samples already in FieldEvents01",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=(
            "Number of samples per `set_values` call when writing annotation "
            f"fields (default: {DEFAULT_CHUNK_SIZE}). Larger values mean fewer "
            "proxy round-trips (one per field when chunk covers all samples)."
        ),
    )
    args = parser.parse_args()
    csv_files = resolve_csv_paths(args.csv_files)

    # ── Step 1: Load all CSV annotations ──────────────────────────────────────
    print("Reading classification CSVs...")
    annotations = load_annotations(csv_files)
    all_event_ids = list(annotations.keys())
    print(f"Total unique events across CSVs: {len(all_event_ids)}\n")

    # ── Step 2: Load source dataset ───────────────────────────────────────────
    print(f"Loading source dataset '{SOURCE_DATASET}'...")
    source = fo.load_dataset(SOURCE_DATASET)

    source_view = source.match(F("event_id").is_in(all_event_ids))
    found_ids = set(source_view.values("event_id"))
    missing_ids = set(all_event_ids) - found_ids

    print(f"Found {len(found_ids)} / {len(all_event_ids)} event_ids in source dataset")
    if missing_ids:
        print(f"  {len(missing_ids)} event_ids not found in source (will be skipped):")
        for eid in sorted(missing_ids)[:10]:
            print(f"    {eid}")
        if len(missing_ids) > 10:
            print(f"    … and {len(missing_ids) - 10} more")
    print()

    # ── Step 3: Create or load target dataset ─────────────────────────────────
    if fo.dataset_exists(TARGET_DATASET):
        target = fo.load_dataset(TARGET_DATASET)
        existing_ids = set(target.values("event_id")) if len(target) > 0 else set()
        print(f"Loaded existing dataset '{TARGET_DATASET}' ({len(target)} samples already present)")
    else:
        target = fo.Dataset(TARGET_DATASET)
        target.persistent = True
        existing_ids = set()
        print(f"Created new persistent dataset '{TARGET_DATASET}'")

    # Ensure typed list fields exist
    schema = target.get_field_schema()
    for field in ("non_person_movement", "out_of_snapshot_activity", "discard_criteria"):
        if field not in schema:
            target.add_sample_field(field, fo.ListField, subfield=fo.StringField())

    # ── Step 4: Add new samples from source into target ───────────────────────
    new_ids = found_ids - existing_ids
    print(f"\n{len(new_ids)} new samples to add, {len(found_ids & existing_ids)} already present")

    if new_ids:
        new_view = source.match(F("event_id").is_in(list(new_ids)))
        new_samples = [s.copy() for s in new_view.iter_samples(progress=True)]
        target.add_samples(new_samples, expand_schema=True)
        print(f"Added {len(new_samples)} new samples into '{TARGET_DATASET}'")

    # ── Step 5: Write annotation fields (set_values per field) ───────────────
    # Each set_values call is one proxy round-trip. With chunk_size >= total,
    # this is exactly N_fields calls — the minimum possible with this API.
    ids_to_annotate = found_ids if args.update_existing else new_ids
    if not ids_to_annotate:
        print("\nNo samples to annotate (use --update-existing to overwrite existing annotations).")
    else:
        label = "all found" if args.update_existing else "newly added"
        print(f"\nWriting annotation fields on {len(ids_to_annotate)} {label} samples...")

        annotation_view = target.match(F("event_id").is_in(list(ids_to_annotate)))
        field_schema = target.get_field_schema()

        ordered_event_ids = annotation_view.values("event_id")
        total = len(ordered_event_ids)

        field_values: dict[str, dict[str, object]] = {f: {} for f in ANNOTATION_FIELDS}
        field_values["annotation_source_csv"] = {}
        for eid in ordered_event_ids:
            ann = annotations.get(eid) or {}
            for field in ANNOTATION_FIELDS:
                field_values[field][eid] = parse_csv_value(
                    ann.get(field, ""), field_schema.get(field), field_name=field
                )
            field_values["annotation_source_csv"][eid] = ann.get("_source_csv", "")

        all_fields = ANNOTATION_FIELDS + ["annotation_source_csv"]
        chunk_size = max(1, args.chunk_size)
        n_chunks = (total + chunk_size - 1) // chunk_size
        print(
            f"  {total} samples, chunk_size={chunk_size} → "
            f"{len(all_fields)} fields × {n_chunks} chunk(s) = {len(all_fields) * n_chunks} proxy call(s)"
        )
        t_start = time.monotonic()

        for i, field in enumerate(all_fields, 1):
            field_start = time.monotonic()
            for chunk_start in range(0, total, chunk_size):
                chunk_ids = ordered_event_ids[chunk_start:chunk_start + chunk_size]
                chunk_values = {eid: field_values[field][eid] for eid in chunk_ids}
                chunk_view = target.match(F("event_id").is_in(chunk_ids))

                t0 = time.monotonic()
                chunk_view.set_values(field, chunk_values, key_field="event_id")
                dt = time.monotonic() - t0

                done = min(chunk_start + chunk_size, total)
                rate = done / max(1e-6, time.monotonic() - field_start)
                eta = (total - done) / rate if rate > 0 else float("inf")
                print(
                    f"  [{i}/{len(all_fields)}] '{field}' {done}/{total}  "
                    f"(chunk {dt:.1f}s, ETA {eta:.0f}s)",
                    flush=True,
                )

        print(f"  Done. {total} samples annotated in {time.monotonic() - t_start:.0f}s")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'─' * 50}")
    print(f"Dataset   : {TARGET_DATASET}")
    print(f"Total samples in dataset: {len(target)}")
    print(f"CSV events loaded  : {len(all_event_ids)}")
    print(f"Found in source    : {len(found_ids)}")
    print(f"Not in source      : {len(missing_ids)}")
    print(f"Newly added today  : {len(new_ids)}")
    print(f"Already present    : {len(found_ids & existing_ids)}")


if __name__ == "__main__":
    main()
