"""Standardise the annotated event CSVs for Google Looker Studio.

Reads each csvs/event_classifications_YYYY_MM_DD.csv, then writes one file per
event_type into csvs_standardized/ using the naming convention:

    annotated_events_{YYYY}_{MM_prev}_{learning|intrusion}.csv

where MM_prev is the calendar month before the MM in the source filename (with
year rollover for January). All output files share the union schema so that a
single Looker Studio data source can consume any of them.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

SRC_DIR = Path("csvs")
OUT_DIR = Path("csvs_standardized")

# Excluded per user request (the mixed 2025_06_30 seed file).
EXCLUDE = {"event_classifications_2025_06_30.csv"}

FILENAME_RE = re.compile(r"^event_classifications_(\d{4})_(\d{2})_(\d{2})\.csv$")

UNION_COLUMNS = [
    "event_id",
    "serial_number",
    "event_type",
    "period",
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
    "fp_cause_group",
    "fn_cause",
    "major_class",
    "intermediary_class",
]

FP_CAUSE_GROUP: dict[str, str] = {
    "extrapolated person": "person nearby",
    "shadow / reflection": "person nearby",
    "non-human object": "object",
    "pet / animal": "object",
    "windscreen: raindrops": "windscreen smudge",
    "windscreen: soap": "windscreen smudge",
    "low light": "light issues",
    "lens flare": "light issues",
}

DROPPED_COLUMNS = {
    "person_in_image",
    "person_visible_in_zone",
    "person_intruding",
    "person_in_bbox",
}

# When discard_criteria is populated, blank these annotation fields and force
# the class columns to "discard".
DISCARD_BLANKED_COLUMNS = [
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
]
DISCARD_CLASS_COLUMNS = ["major_class", "intermediary_class"]
DISCARD_CLASS_VALUE = "discard"

# Per-column value fix-ups to enforce consistent vocabulary. Applied after the
# discard rule so blanked cells are never touched.
VALUE_FIXES: dict[str, dict[str, str]] = {
    "fp_cause": {
        # Match the "space around slash" convention used elsewhere in the schema
        # (hand / arm, pet / animal, garage door opening / closing, ...).
        "shadow/reflection": "shadow / reflection",
    },
}


def apply_value_fixes(row: dict[str, str]) -> dict[str, str]:
    for col, mapping in VALUE_FIXES.items():
        v = (row.get(col) or "").strip()
        if v in mapping:
            row[col] = mapping[v]
    return row


def apply_fp_cause_group(row: dict[str, str]) -> dict[str, str]:
    """Populate fp_cause_group from fp_cause via FP_CAUSE_GROUP mapping.
    Unknown/blank fp_cause -> blank group. Fail loudly on unmapped values so
    new causes don't silently slip through as ungrouped."""
    cause = (row.get("fp_cause") or "").strip()
    if not cause:
        row["fp_cause_group"] = ""
        return row
    if cause not in FP_CAUSE_GROUP:
        raise RuntimeError(
            f"fp_cause={cause!r} is not in FP_CAUSE_GROUP mapping. "
            f"Add it to FP_CAUSE_GROUP in standardize_csvs_for_looker.py."
        )
    row["fp_cause_group"] = FP_CAUSE_GROUP[cause]
    return row


def apply_discard_rule(row: dict[str, str]) -> dict[str, str]:
    """If discard_criteria is set, blank downstream annotation fields and force
    major_class/intermediary_class to "discard"."""
    if (row.get("discard_criteria") or "").strip():
        for col in DISCARD_BLANKED_COLUMNS:
            row[col] = ""
        for col in DISCARD_CLASS_COLUMNS:
            row[col] = DISCARD_CLASS_VALUE
    return row


def prev_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def target_key(event_type: str) -> str:
    et = event_type.strip().upper()
    if et == "LEARNING":
        return "learning"
    if et == "INTRUSION":
        return "intrusion"
    raise ValueError(f"Unexpected event_type: {event_type!r}")


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    # buckets keyed by output filename -> list of row dicts (already keyed on union columns)
    buckets: dict[str, list[dict[str, str]]] = {}

    for src in sorted(SRC_DIR.glob("*.csv")):
        if src.name in EXCLUDE:
            print(f"skipping {src.name} (excluded)")
            continue

        match = FILENAME_RE.match(src.name)
        if not match:
            print(f"skipping {src.name} (filename does not match pattern)")
            continue

        year, month, _day = (int(g) for g in match.groups())
        out_year, out_month = prev_month(year, month)
        prefix = f"annotated_events_{out_year}_{out_month:02d}"

        with src.open(newline="") as fh:
            reader = csv.DictReader(fh)
            src_cols = reader.fieldnames or []
            unexpected = [
                c for c in src_cols if c not in UNION_COLUMNS and c not in DROPPED_COLUMNS
            ]
            if unexpected:
                raise RuntimeError(
                    f"{src.name} has unexpected columns not in the union schema: {unexpected}"
                )

            for row in reader:
                normalised = {col: row.get(col, "") for col in UNION_COLUMNS}
                # YYYYMM (e.g. 202604) so Looker Studio auto-detects it as
                # Year Month type. From there LS can derive year/month directly.
                normalised["period"] = f"{out_year:04d}{out_month:02d}"
                normalised = apply_discard_rule(normalised)
                normalised = apply_value_fixes(normalised)
                normalised = apply_fp_cause_group(normalised)
                event_type = normalised["event_type"]
                key = target_key(event_type)
                out_name = f"{prefix}_{key}.csv"
                buckets.setdefault(out_name, []).append(normalised)

    for out_name in sorted(buckets):
        rows = buckets[out_name]
        out_path = OUT_DIR / out_name
        with out_path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=UNION_COLUMNS, quoting=csv.QUOTE_MINIMAL)
            writer.writeheader()
            writer.writerows(rows)
        types = {r["event_type"] for r in rows}
        print(f"wrote {out_path} ({len(rows)} rows, event_types={sorted(types)})")


if __name__ == "__main__":
    main()
