# IDW hybrid comparison — PM2.5 ds-v0.3.0

| protocol    | method        |    n |     mae |    rmse |    bias |      r2 |
|:------------|:--------------|-----:|--------:|--------:|--------:|--------:|
| fixed_split | IDW           |  689 | 13.9508 | 18.7448 | -8.2723 |  0.1409 |
| fixed_split | IDW + XGBoost |  689 | 13.7869 | 18.4782 | -7.5532 |  0.1652 |
| fixed_split | IDW + ST-GNN  |  689 | 13.704  | 18.3721 | -7.4394 |  0.1747 |
| loso        | IDW           | 4358 | 17.322  | 23.9954 | -1.5685 | -0.0398 |
| loso        | IDW + XGBoost | 4358 | 16.6418 | 23.2878 | -0.631  |  0.0206 |
| loso        | IDW + ST-GNN  | 4358 | 17.8607 | 23.7403 |  2.9072 | -0.0178 |

## Protocol

IDW uses p=2 and at most 8 source stations at the current hour. Fixed split uses train stations as sources. LOSO holds out entire station groups from training, validation and every sensor-derived feature; other groups supply current-hour readings. Training uses the train time block; model selection uses the validation time block. Test uses the held group in the test time block. All three methods have identical test keys and observations within each protocol; fixed split and LOSO have different test populations.

Training rows without any eligible IDW source are excluded, matching ST-GNN. Their counts are recorded per fold. Validation and test rows are never dropped.

XGBoost learns a correction to IDW from 54 tabular features, including spatial interpolation, sensor geometry, satellite, weather, GEOS-CF analysis/forecast, calendar and coordinates. No row-shift lags are used. For LOSO, depths 2/4/6 and early stopping are selected separately on non-held groups' validation data. A zero correction is eligible. Learning rate=0.03, maximum 1500 trees, patience=50, min_child_weight=20, reg_lambda=10, subsample=0.8, colsample_bytree=0.8, seed=42. Fixed split reuses the validation-selected residual model from the earlier six-candidate direct/residual experiment.

ST-GNN reuses the completed GPU runs: 58 temporal channels, 8 local channels, TCN + local MLP + GAT + residual regression. Thus this compares pipelines with different feature representations and selection budgets, not architecture alone.

## Paired uncertainty

| protocol    | method        | baseline     |   ci_low |   ci_high |   groups_better |   groups_total |
|:------------|:--------------|:-------------|---------:|----------:|----------------:|---------------:|
| fixed_split | IDW + XGBoost | IDW          |  -0.435  |   -0.0447 |              11 |             15 |
| fixed_split | IDW + ST-GNN  | IDW          |  -0.5947 |   -0.1054 |              11 |             15 |
| fixed_split | IDW + XGBoost | IDW + ST-GNN |   0.0019 |    0.2015 |               7 |             15 |
| loso        | IDW + XGBoost | IDW          |  -2.9016 |    1.4302 |              56 |             95 |
| loso        | IDW + ST-GNN  | IDW          |  -2.3126 |    1.8483 |              43 |             95 |
| loso        | IDW + XGBoost | IDW + ST-GNN |  -2.1161 |    1.1403 |              55 |             95 |

95% percentile intervals for RMSE(method) − RMSE(baseline), using 4,000 paired resamples of station groups, seed=0. Negative values favor the hybrid. Intervals do not capture training-seed variability.

## Interpretation

ST-GNN has the lowest fixed-split MAE/RMSE among these pipelines. XGBoost has the lowest LOSO MAE/RMSE among these pipelines. Both LOSO hybrid-versus-IDW RMSE intervals include zero, so these point estimates do not establish a reliable improvement. For context, the previously evaluated IDW p=1/all baseline has LOSO RMSE 22.1785, below all three pipelines in the main table. The main table consistently uses p=2/k=8.

## Limitations

Single seed and previously inspected test data: exploratory results. No new ST-GNN training was performed. The input representations and tuning budgets differ.
