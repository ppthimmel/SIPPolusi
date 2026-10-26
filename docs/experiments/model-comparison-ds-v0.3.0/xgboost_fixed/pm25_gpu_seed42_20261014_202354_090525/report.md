# XGBoost PM2.5 fixed split

| split   | method     | station   |    n |     mae |    rmse |     bias |       r2 |
|:--------|:-----------|:----------|-----:|--------:|--------:|---------:|---------:|
| val     | idw_p2_k8  | pooled    | 1008 | 14.3453 | 19.9697 | -4.36458 | 0.251134 |
| val     | idw_p1_all | pooled    | 1008 | 13.5778 | 18.8542 | -1.97349 | 0.332459 |
| val     | xgboost    | pooled    | 1008 | 14.2258 | 19.7791 | -3.60547 | 0.265356 |
| test    | idw_p2_k8  | pooled    |  689 | 13.9508 | 18.7448 | -8.2723  | 0.140886 |
| test    | idw_p1_all | pooled    |  689 | 15.6078 | 18.8794 | -7.13216 | 0.128509 |
| test    | xgboost    | pooled    |  689 | 13.7869 | 18.4782 | -7.5532  | 0.165151 |

Six preset direct/residual configurations, selected on validation with early stopping. Train-station readings only, excluding each target group. No lag features. Single seed; exploratory results on a previously inspected test dataset.
