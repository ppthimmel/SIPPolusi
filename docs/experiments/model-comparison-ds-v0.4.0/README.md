# Model comparison on ds-v0.4.0 (all folds)

Run on 20 Nov 2026 with `notebooks/spatial_model/train_all_models_colab.ipynb` on Google Colab (L4 GPU), using the
data bundle `sippolusi_ds-v0.4.0_bundle.zip`. All LOSO folds: 109 for PM2.5, 15 for NO₂.

## Setup

| Item | Value |
|---|---|
| Dataset | ds-v0.4.0: ground truth 13 Sep – 19 Nov 2026 (Railway + GCP), labels 11–16 Nov excluded (collection gap) |
| Blocks | train 13 Sep – 28 Oct, validation 29 Oct – 5 Nov, test 6 – 19 Nov 2026 |
| Protocols | **LOSO** (one station group hidden per fold; main), **fixed split** (PM2.5: station split × block), **temporal split** (NO₂: all stations, later block) |
| Selection | The model with the lowest **validation** RMSE is selected; its **test** score is the reported result |
| Validation in LOSO | The hidden group in the validation block, the same rows for every model. ST-GNN's early stopping uses the visible groups only |
| IDW reference | IDW p=1 all, the IDW with the lower validation RMSE for every target and protocol |
| Models | IDW p=2 k=8, IDW p=1 all; ST-GNN, LightGBM, XGBoost, each **direct** and **+ IDW corr** (predicts label − IDW p=2 k=8, then adds IDW back) |
| Hyperparameters | LightGBM from `configs/lightgbm.json` (chosen on ds-v0.3.0); XGBoost `n_estimators=600, learning_rate=0.03, max_depth=4, min_child_weight=20, subsample=0.8, colsample_bytree=0.5, reg_lambda=5` (not tuned); ST-GNN from `configs/stgnn.json`, up to 100 epochs with early stopping (patience 12) |
| Units | RMSE and MAE in µg/m³ |

## Result to report

| Target | Protocol | Selected model | Test RMSE | Test MAE | Test R² | IDW p=1 all test RMSE | vs IDW |
|---|---|---|---|---|---|---|---|
| **NO₂** | **LOSO** | **XGBoost + IDW corr** | **37.65** | 26.16 | **0.43** | 47.28 | **−9.63 (−20%)** |
| NO₂ | Temporal split ¹ | XGBoost + IDW corr | 21.28 | 13.57 | 0.82 | 47.28 | not comparable ¹ |
| **PM2.5** | **LOSO** | **IDW p=1 all** | **23.01** | 17.63 | 0.24 | 23.01 | 0 |
| PM2.5 | Fixed split | IDW p=1 all | 26.54 | 18.82 | 0.11 | 26.54 | 0 |

★ = selected on validation. **vs IDW** = test RMSE − test RMSE of IDW p=1 all; negative = better than IDW.

¹ The temporal split is **not valid for places without a sensor** and has no "vs IDW" verdict: the models train on
the same stations' history and can recognise a station (from `easting`/`northing` and the geometry features), so
they learn each station's bias, which IDW cannot use. LOSO is the result for unsensed locations.

## NO₂, LOSO (main) — val n = 2,005, test n = 2,354

| Model | Val RMSE | Val MAE | Val R² | Test RMSE | Test MAE | Test R² | vs IDW | Beats IDW |
|---|---|---|---|---|---|---|---|---|
| ★ XGBoost + IDW corr | 35.95 | 26.65 | 0.49 | 37.65 | 26.16 | 0.43 | −9.63 | ✅ |
| LightGBM | 36.21 | 27.44 | 0.48 | 36.71 | 25.70 | 0.46 | −10.57 | ✅ |
| LightGBM + IDW corr | 37.40 | 27.75 | 0.45 | 37.67 | 26.00 | 0.43 | −9.62 | ✅ |
| XGBoost | 38.42 | 28.12 | 0.42 | 39.41 | 27.65 | 0.38 | −7.87 | ✅ |
| ST-GNN | 41.25 | 28.93 | 0.33 | 44.39 | 28.89 | 0.21 | −2.89 | ✅ |
| ST-GNN + IDW corr | 41.39 | 27.95 | 0.32 | 42.48 | 26.90 | 0.28 | −4.80 | ✅ |
| IDW p=1 all | 47.60 | 39.59 | 0.11 | 47.28 | 36.11 | 0.10 | reference | |
| IDW p=2 k=8 | 50.84 | 42.44 | −0.02 | 47.91 | 36.56 | 0.08 | | |

## NO₂, temporal split — val n = 2,005, test n = 2,354

Tests a later period at stations the model trained on, not a new location. **These scores do not apply to places
without a sensor**, and they are not compared with IDW: the tree models learn each station's bias from its own
history (a station is recognisable from `easting`/`northing` and the geometry features), while IDW uses no history.
The sensor features still exclude the station's own group, which is why the IDW rows equal the LOSO ones. ST-GNN does
not support this protocol (it needs held-out stations).

| Model | Val RMSE | Val MAE | Val R² | Test RMSE | Test MAE | Test R² |
|---|---|---|---|---|---|---|
| ★ XGBoost + IDW corr | 17.65 | 12.08 | 0.88 | 21.28 | 13.57 | 0.82 |
| XGBoost | 17.74 | 12.06 | 0.88 | 21.59 | 13.43 | 0.81 |
| LightGBM | 20.12 | 13.81 | 0.84 | 24.17 | 15.20 | 0.77 |
| LightGBM + IDW corr | 20.32 | 14.08 | 0.84 | 23.65 | 15.10 | 0.78 |
| IDW p=1 all | 47.60 | 39.59 | 0.11 | 47.28 | 36.11 | 0.10 |
| IDW p=2 k=8 | 50.84 | 42.44 | −0.02 | 47.91 | 36.56 | 0.08 |

## PM2.5, LOSO (main) — val n = 15,871, test n = 18,264

| Model | Val RMSE | Val MAE | Val R² | Test RMSE | Test MAE | Test R² | vs IDW | Beats IDW |
|---|---|---|---|---|---|---|---|---|
| ★ IDW p=1 all | 22.13 | 17.10 | 0.18 | 23.01 | 17.63 | 0.24 | reference | |
| LightGBM | 22.81 | 17.50 | 0.12 | 24.40 | 18.37 | 0.15 | +1.39 | ❌ |
| LightGBM + IDW corr | 23.06 | 17.27 | 0.10 | 24.28 | 18.05 | 0.15 | +1.28 | ❌ |
| XGBoost | 23.63 | 17.66 | 0.06 | 25.26 | 18.86 | 0.08 | +2.25 | ❌ |
| XGBoost + IDW corr | 23.84 | 17.80 | 0.04 | 25.18 | 18.72 | 0.09 | +2.18 | ❌ |
| IDW p=2 k=8 | 24.17 | 17.61 | 0.02 | 24.19 | 17.32 | 0.16 | | |
| ST-GNN + IDW corr | 26.34 | 19.46 | −0.17 | 25.97 | 18.94 | 0.03 | +2.97 | ❌ |
| ST-GNN | 26.67 | 19.73 | −0.20 | 26.55 | 19.58 | −0.01 | +3.54 | ❌ |

## PM2.5, fixed split — val n = 2,428, test n = 2,517

| Model | Val RMSE | Val MAE | Val R² | Test RMSE | Test MAE | Test R² | vs IDW | Beats IDW |
|---|---|---|---|---|---|---|---|---|
| ★ IDW p=1 all | 22.91 | 16.57 | 0.12 | 26.54 | 18.82 | 0.11 | reference | |
| ST-GNN | 24.58 | 19.07 | −0.01 | 29.28 | 20.77 | −0.09 | +2.73 | ❌ |
| LightGBM | 24.69 | 17.55 | −0.02 | 29.36 | 21.25 | −0.10 | +2.81 | ❌ |
| LightGBM + IDW corr | 25.10 | 17.50 | −0.05 | 29.92 | 21.70 | −0.14 | +3.38 | ❌ |
| ST-GNN + IDW corr | 25.46 | 17.51 | −0.08 | 29.86 | 21.95 | −0.13 | +3.32 | ❌ |
| IDW p=2 k=8 | 26.12 | 16.53 | −0.14 | 29.45 | 20.86 | −0.10 | | |
| XGBoost + IDW corr | 26.32 | 16.52 | −0.16 | 33.60 | 25.33 | −0.43 | +7.06 | ❌ |
| XGBoost | 26.37 | 17.74 | −0.16 | 31.78 | 23.72 | −0.28 | +5.24 | ❌ |

## Conclusion

- **NO₂:** every learned model beats IDW in LOSO. The tree models are best (about −20% RMSE, R² 0.43–0.46 against
  0.10). XGBoost + IDW corr is selected on validation; LightGBM direct, the current production model, is close to it
  (val 36.21 against 35.95; test 36.71 against 37.65). No confidence interval was computed for that difference, so
  this run does not show which of the two is better. XGBoost was not tuned, while LightGBM's hyperparameters were
  tuned on ds-v0.3.0.
- **PM2.5:** nothing beats **IDW p=1 all**, which is also better than the IDW p=2 k=8 production fallback (test
  23.01 against 24.19). The learned models are 1.3–3.5 µg/m³ worse in LOSO. 3.6× more data than ds-v0.3.0 did not
  change this.
- Same conclusions as ds-v0.3.0 ([report](../model-comparison-ds-v0.3.0/README.md)): PM2.5 selected IDW p=1 all
  (LOSO test 22.18), NO₂ selected LightGBM direct. The numbers are not directly comparable: test periods (late
  September against November) and station split differ.

## Reproducibility

The tables above can be checked from this folder:

| Path | Content |
|---|---|
| [`results/results.csv`](results/results.csv) | Every metric (n, MAE, RMSE, R², bias) per target, protocol, model and split, written by the notebook run |
| [`results/summary.csv`](results/summary.csv), [`results/report.csv`](results/report.csv) | Derived from `results.csv` with the notebook's summary cell (section 7) at this commit |
| [`snapshot/manifest.json`](snapshot/manifest.json) | Snapshot manifest: input and output hashes, counts, code commit `caaa113` |
| [`snapshot/CHECKSUMS.sha256`](snapshot/CHECKSUMS.sha256) | SHA-256 of every snapshot file (the notebook checks these before training) |
| [`snapshot/config.toml`](snapshot/config.toml), [`snapshot/split_spec.json`](snapshot/split_spec.json), [`snapshot/split_stats.md`](snapshot/split_stats.md), [`snapshot/exclusion_rules.md`](snapshot/exclusion_rules.md) | Blocks, groups, station split, label rules and exclusion counts |
| [`snapshot/leakage_report.json`](snapshot/leakage_report.json), [`.md`](snapshot/leakage_report.md) | The 8 leakage checks, all passed |
| [`snapshot/sippolusi_ds-v0.4.0_bundle.zip.sha256`](snapshot/sippolusi_ds-v0.4.0_bundle.zip.sha256) | SHA-256 of the data bundle used for this run (`8a84e66b…`, LZMA zip of the snapshot above) |
| [`backfill/BACKFILL_MANIFEST.json`](backfill/BACKFILL_MANIFEST.json) | Oct–Nov feature backfill: per-source reports and the overlap comparison with the original `dataset_processed` |

The snapshot itself (16 MB) is not committed. It is rebuilt with
[`src/data_worker/scripts/dataset_extension`](../../../src/data_worker/scripts/dataset_extension/README.md) and checked
against `snapshot/CHECKSUMS.sha256`.

**Backfill provenance.** The GEOS-CF, Open-Meteo, Sentinel-5P and Landsat backfill ran as a Cloud Run Job from a code
copy in `gs://sippolusi-seed-87531247158/backfill/code/`, not from the container image. That copy was compared file by
file (MD5) with git: `connectors/`, `feature_matrix/`, `feature_store/`, `scripts/` and `spatial_model/` are identical
to commit `9c8f507` (96 files plus two empty `__init__.py`), and `backfill_features.py` is identical to
`src/data_worker/scripts/dataset_extension/backfill_features.py` as committed in `caaa113`.

## Notes

- **Correction applied to `beats_idw`.** An earlier version of the summary compared ST-GNN with IDW p=2 k=8 (its
  paired IDW) and the other models with the IDW best on test. All models are now compared with the IDW chosen on
  validation (IDW p=1 all). The only verdict that changed: PM2.5 fixed split, ST-GNN, from beats IDW to does not
  (29.28 is better than IDW p=2 k=8 at 29.45, but worse than IDW p=1 all at 26.54). The tables above use the
  corrected reference.
- **LOSO numbers depend on the fold set.** A run with only folds 0–4 tests 5 station groups and gives different
  numbers and a different selection; compare models only within one fold set. These are all folds.
- **LOSO validation and test** are at the same hidden station in different periods (≥ 24 h apart); no model trains on
  that station.
- **Inputs:** VIIRS was not refreshed after 20 Sep (needs an Earthdata token), so Oct–Nov use the September value
  with its age; cloudy weather also makes Landsat NDVI/NDBI older in Oct–Nov.
- **Station metadata:** DKJ32–35 are now `Reference` (were `Sensor` in the ds-v0.3.0 export), which moved the station
  split.
- Outputs on Drive: `SIPPolusi/runs/results.csv`, `summary.csv`, `report.csv`, per-fold models and predictions.
