# Extending the spatial-model dataset (ds-v0.4.0)

These scripts built ds-v0.4.0 (13 Sep – 19 Nov 2026) from the ground truth in the Railway and GCP databases.
Run them again to add more weeks and build the next version. Run everything from `src/data_worker` with
`PYTHONPATH=.`. Keep inputs and outputs outside the repository.

| Step | Script | Input → output |
|---|---|---|
| 1. Ground truth | `convert_ground_truth_csv.py OUT CSV_DIR [CSV_DIR ...]` | Gzipped CSV dumps of the `ground_truth` schema (one folder per database; later folders win on duplicate keys) → `station.parquet`, `observation.parquet`, `manifest.json` in the old export format. It recomputes `in_jakarta_bbox` with the rule of the 20261006 export. |
| 2. Features | `backfill_features.py --start --end --out [--all \| --group N]` | The Data Worker connectors → `dataset_processed`-format files for the period. Groups: 0 GEOS-CF ana, 1 GEOS-CF fcst, 2 Open-Meteo + Sentinel-5P, 3 Landsat. VIIRS is not included (it needs `EARTHDATA_TOKEN`). |
| 3. Merge | `merge_backfill.py ORIGINAL BACKFILL OUT` | A copy of `dataset_processed` with the backfill added. The original wins on any overlap, and the overlap is compared in `BACKFILL_MANIFEST.json`. |
| 4. Snapshot | `python -m spatial_model.data.snapshot build --ground-truth <1> --dataset-processed <3> --config spatial_model/configs/ds-v0.4.0.toml --out <new folder>` | The frozen snapshot. |
| 5. Colab | `make_colab_bundle.py SNAPSHOT OUT.zip`, `make_colab_notebook.py OUT.ipynb` | The data-only bundle and the self-contained `notebooks/spatial_model/train_all_models_colab.ipynb`. After changing model code, run `check_notebook_parity.py NOTEBOOK SNAPSHOT src/data_worker` to confirm the notebook still matches the repository. |

## Running the feature backfill on GCP

NASA's GEOS-CF servers (`portal.nccs.nasa.gov`, `opendap.nccs.nasa.gov`) were unreachable from the development
laptop, but GCP can reach them. `feature-backfill.job.yaml` runs step 2 as a Cloud Run Job with four parallel
tasks on the data-worker image. The code is read from `gs://sippolusi-seed-87531247158/backfill/code/` (upload
`connectors`, `feature_matrix`, `feature_store`, `scripts`, `spatial_model` and this script there), and the output
goes to `gs://sippolusi-seed-87531247158/backfill/out/<stamp>/`. The 19 Nov run took about 20 minutes. Delete
the job afterwards.

## Checks used for ds-v0.4.0

- Rebuilding ds-v0.3.0 with its own config from the converted Railway data reproduces the committed snapshot:
  identical labels, splits, folds, normalisation and leakage report. That holds with the 6 Oct station table; with the
  current one, the station split moves because DKJ32–35 are now `Reference`.
- The backfill overlap with the original files (27–30 Sep) is identical for GEOS-CF, Open-Meteo and Sentinel-5P.
  The 22 Sep Landsat scene differs in 4 of about 8,966 valid cells, because of the connector's aerosol mask.
- Ground-truth timestamps are UTC: SOLAR_RAD peaks at 03–05 UTC and air temperature is lowest at 22 UTC.
