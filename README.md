# FiftyOne Metrics

Utilities and reference data for sampling, annotating, and analyzing FiftyOne intrusion-event datasets.

This workspace is currently centered around the end-to-end event review workflow:

1. Sampling intrusion events from a source FiftyOne dataset
2. Classifying sampled events in the review interface
3. Importing classified CSV annotations into `FieldEvents01`
4. Producing monthly false-positive-rate analysis from annotated CSV exports

## Layout

- `import_annotations_to_fieldevents01.py`
  Imports classified CSV annotations into `FieldEvents01`.

- `run_sample.py`
  Queries eligible devices from Redshift and draws a stratified monthly intrusion sample from FiftyOne.

- `monthly_fpr_analysis.py`
  Generates monthly false-positive-rate summaries and field breakdowns from annotation CSVs.

- `event-classification-per-event.html`
  Core classification interface for the per-event review step in the workflow.

- `csvs/`
  Source classification CSV exports.

- `docs/`
  Supporting reference material and annotation framework assets.

## Workflow

The intended operating flow is:

1. Sample events with `run_sample.py`
2. Classify them in `event-classification-per-event.html`
3. Import the resulting CSV annotations with `import_annotations_to_fieldevents01.py`
4. Generate metrics with `monthly_fpr_analysis.py`

- `outputs/`
  Generated samples, exports, plots, and reports.

- `archive/`
  Older one-off analysis scripts and notebook work.

## Annotation Import Workflow

The annotation import script accepts one or more classification CSVs via repeated `--csv` flags.

For each `event_id`, it:

1. Loads the sample from `prod_intrusion_event_datalake_anonymized`
2. Adds the sample to `FieldEvents01` if it is not already there
3. Writes the CSV annotation fields onto the sample
4. Stores the source CSV filename in `annotation_source_csv`

Run it with:

```bash
python3 import_annotations_to_fieldevents01.py \
  --csv csvs/event_classifications_2026_02_09.csv \
  --csv csvs/event_classifications_2026_03_08.csv \
  --csv csvs/event_classifications_2026_04_13.csv \
  --update-existing
```

Use `--update-existing` when samples are already in `FieldEvents01` and you want to refresh their annotation fields.

## Environment Notes

This workspace currently uses local credentials loaded from `.env` before importing FiftyOne.

Expected external dependencies include:

- FiftyOne / FiftyOne Teams access
- Redshift credentials for sampling work
- Python packages used by the scripts, including `fiftyone`, `pandas`, `numpy`, `matplotlib`, and `psycopg2`

In this environment, `fiftyone` is available via `python3`, not the local `.venv`.

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
- `docs/annotation_framework.txt`
- selected CSV inputs in `csvs/`

## Likely Untracked Files

These are typically local or generated and usually should not be committed:

- `.env`
- `.venv/`
- `.venv312/`
- `outputs/`
- Finder metadata files