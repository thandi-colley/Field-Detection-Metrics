# FiftyOne Metrics

Utilities and reference data for sampling, annotating, and analyzing FiftyOne event datasets (intrusion and learning).

This workspace is currently centered around the end-to-end event review workflow:

1. Sampling **intrusion** and **learning** events from a source FiftyOne dataset
2. Classifying sampled events in the review interface
3. Importing classified CSV annotations into `FieldEvents01`
4. Producing monthly false-positive-rate analysis from annotated CSV exports

## Layout

- `import_annotations_to_fieldevents01.py`
  Imports classified CSV annotations into `FieldEvents01`.

- `run_sample.py`
  Queries eligible devices from Redshift and draws a stratified monthly sample from FiftyOne.
  Supports `--event-type INTRUSION` (default) or `--event-type LEARNING`.

- `monthly_fpr_analysis.py`
  Generates monthly false-positive-rate summaries and field breakdowns from annotation CSVs.
  Supports `--event-type INTRUSION` (default) or `--event-type LEARNING`.

- `add_serial_numbers.py`
  Inserts a `serial_number` column into a classification CSV by joining several
  Redshift tables (no FiftyOne needed). Optional pre-step before importing.

- `event-classification-per-event.html`
  Core classification interface for the per-event review step in the workflow.

- `csvs/`
  Source classification CSV exports.

- `docs/`
  Supporting reference material and annotation framework assets.

## Workflow

The intended operating flow is:

1. Sample events with `run_sample.py` — run **twice per month**, once for each event type:
   ```bash
   .venv312/bin/python run_sample.py --year 2026 --month 4 --event-type INTRUSION
   .venv312/bin/python run_sample.py --year 2026 --month 4 --event-type LEARNING
   ```
  By default, sampling covers the final 28 days of the specified month. Use `--start-day` and `--end-day` to override the range.
2. Classify them in the classification interface (see [Classification interface](#classification-interface))
3. (Optional) Add `serial_number` to the CSV via `add_serial_numbers.py` (see [Annotation Import Workflow](#annotation-import-workflow))
4. Import the resulting CSV annotations with `import_annotations_to_fieldevents01.py`
5. Generate metrics with `monthly_fpr_analysis.py` — run **twice per month**, once for each event type:
   ```bash
   .venv312/bin/python monthly_fpr_analysis.py --current csvs/event_classifications_2026_04_13.csv --event-type INTRUSION
   .venv312/bin/python monthly_fpr_analysis.py --current csvs/event_classifications_2026_04_13.csv --event-type LEARNING
   ```

- `outputs/`
  Generated samples, exports, plots, and reports.

- `archive/`
  Older one-off analysis scripts and notebook work.

## Classification interface

Open `event-classification-per-event.html` in a browser from the repo root:

```bash
open event-classification-per-event.html
```

On Linux or Windows, or if you prefer a local URL:

```bash
python3 -m http.server 8765
```

Then open [http://localhost:8765/event-classification-per-event.html](http://localhost:8765/event-classification-per-event.html).

Usage:

1. Paste the full contents of a sample JSON from `outputs/samples/` (e.g. `outputs/samples/2026-05_intrusion_sample.json`) into the text box.
2. Click **Load Events**, choose **INTRUSION** or **LEARNING**, and classify each event.
3. Click **Export CSV** and save the file under `csvs/` (e.g. `csvs/event_classifications_2026_05_08.csv`).

Progress is auto-saved in the browser (`localStorage`) until you click **Clear Saved Data**.

## Annotation Import Workflow

The annotation import script accepts one or more classification CSVs via repeated `--csv` flags.

For each `event_id`, it:

1. Loads the sample from `prod_intrusion_event_datalake_anonymized`
2. Adds the sample to `FieldEvents01` if it is not already there
3. Writes the CSV annotation fields onto the sample
4. Stores the source CSV filename in `annotation_source_csv`

Run it with:

```bash
.venv312/bin/python import_annotations_to_fieldevents01.py \
  --csv csvs/event_classifications_2026_02_09.csv \
  --csv csvs/event_classifications_2026_03_08.csv \
  --csv csvs/event_classifications_2026_04_13.csv \
  --update-existing
```

Use `--update-existing` when samples are already in `FieldEvents01` and you want to refresh their annotation fields.

### Adding `serial_number` to a CSV (optional pre-step)

`add_serial_numbers.py` inserts a `serial_number` column right after `event_id` by joining three Redshift tables (`events_ended_and_classified_security_events` ∪ `events_security_events_image_evidence` → `canopy_services_user_device` → `v_normalized_ids`). Uses Redshift only, no FiftyOne required:

```bash
.venv312/bin/python add_serial_numbers.py --csv csvs/event_classifications_2026_06_07.csv
```

Pass `--output PATH` to write a copy instead of overwriting the input. The script prints a summary of how many rows resolved vs. were missing.

## Environment Notes

The Python interpreter to use is `.venv312/bin/python` (Python 3.12). All FiftyOne / Redshift scripts must be invoked with that interpreter — system `python3` and the older `.venv` are not configured for this workflow.

All scripts load credentials from `.env` *before* importing `fiftyone` (FiftyOne reads its config at import time, so this ordering matters — don't move the imports to the top).

### `.env` keys

```
# FiftyOne Teams API (used by import / sampling)
FIFTYONE_API_URI=https://ml.opetics.com
FIFTYONE_API_KEY=...

# Redshift (used by run_sample.py and add_serial_numbers.py)
REDSHIFT_HOST=10.0.0.48
REDSHIFT_PORT=5439
REDSHIFT_DATABASE=prod
REDSHIFT_USER=...
REDSHIFT_PASSWORD=...
```

### FiftyOne Teams SDK setup

The open-source `fiftyone` package on PyPI **cannot** talk to a FiftyOne Teams server — its connection check refuses with `Cannot connect to database type 'fiftyone-teams' with client type 'fiftyone'`. You must install the Teams variant from Voxel51's private PyPI:

```bash
.venv312/bin/python -m pip install \
  --index-url https://<VOXEL51_PYPI_TOKEN>@pypi.fiftyone.ai \
  fiftyone==2.16.4
```

The version (`2.16.4`) should match the Teams server version (see `~/Desktop/fiftyone-teams-local/docker-compose.yml` → `voxel51/fiftyone-teams-app:vX.Y.Z`).

The `VOXEL51_PYPI_TOKEN` is the org-wide install token from Voxel51 onboarding — pull it from the team's internal docs (search for "Configuring FiftyOne with Direct DB Connection" — the install command at the bottom of Raj's sample notebook contains the token in the URL).

Verify the install with:

```bash
.venv312/bin/python -c "import fiftyone.constants as foc; print(foc.CLIENT_TYPE)"
# expected: fiftyone-teams
```

### Other dependencies

Beyond the Teams SDK, the scripts use:

- `pandas`, `numpy`, `matplotlib` — sampling + plots
- `psycopg2` (install `psycopg2-binary` for the easy path) — Redshift connection

The `.venv312` was created when this folder was named `FiftyOne Metrics` and then renamed to `Field Detection Metrics`, which breaks the venv's `pip` shebang. Always install via `.venv312/bin/python -m pip install …` (NOT `.venv312/bin/pip …`) to dodge the broken shebang.

## Output Locations

Generated files now go under `outputs/` instead of the repository root:

- `outputs/samples/`
- `outputs/exports/`
- `outputs/plots/`
- `outputs/reports/`

`run_sample.py` writes sample JSON and sampling plots there by default, and `monthly_fpr_analysis.py` writes reports to `outputs/reports/` by default.

## Repo Hygiene

The root `.gitignore` excludes:

- local env files
- local virtual environments
- Python cache files
- Finder metadata
- generated outputs under `outputs/`

This keeps the repo focused on source scripts and input data rather than local runtime artifacts.

## Remaining Setup

Before opening a merge request, you should still:

1. Review the initial Git add set and confirm the tracked CSVs are the ones you want in source control
2. Add the remote and create the first commit on the target branch
3. Decide whether `MCP.code-workspace` should stay ignored or move into a shared editor config approach later

## Likely Tracked Files

These are the files most likely to belong in source control:

- `README.md`
- `.gitignore`
- `import_annotations_to_fieldevents01.py`
- `run_sample.py`
- `monthly_fpr_analysis.py`
- `add_serial_numbers.py`
- `event-classification-per-event.html`
- `docs/annotation_framework.txt`
- selected CSV inputs in `csvs/`

## Likely Untracked Files

These are typically local or generated and usually should not be committed:

- `.env`
- `.venv/`
- `.venv312/`
- `outputs/`
- Finder metadata files