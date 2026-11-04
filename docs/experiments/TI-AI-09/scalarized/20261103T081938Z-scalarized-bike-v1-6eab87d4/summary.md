# 20261103T081938Z-scalarized-bike-v1-6eab87d4

- **Status:** `early_stopped`: No validation improvement in 5 evaluations
- **Algorithm:** `ppo-scalarized-1`; seed 42; 87,043 parameters; config SHA-256 `6eab87d402d9`
- **Code:** commit `6efe87f826fff341fadef47d74d8b1b7f39a064c`, uncommitted changes: False
- **Graph:** `jakarta-20261005`, bike; fields manifest SHA-256 `7329a154a9fa`; OD set SHA-256 `a420b08fd3b7`
- **Progress:** 150 updates, 307,200 environment steps, 4,001 episodes
- **Selection:** validation mean_return (greedy policy; failure = -failure_return), split `val`, α ∈ [0.0, 0.25, 0.5, 0.75, 1.0]; test split loaded: False
- **Selected checkpoint:** update 0, validation return -1.0003, reached 100.0%, SHA-256 `28709b934d26`
- **Note:** the selected checkpoint is the warm start. PPO on the scalarized reward did not improve validation return.
- **Reload check:** checksum matches: True; 750 routes identical across two loads: True; median 0.452 s per route on CPU

## Validation, real fields

1650 episodes; reached 100.0%; optimality gap 0.08%. These episodes also selected the checkpoint, so the numbers are slightly optimistic; TI-AI-10 reports on test.

| α | β | Reached | Mean return | T/T_ref | E/E_ref | Optimal T/T_ref | Optimal E/E_ref |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 1 | 100.0% | -0.999 | 1.003 | 0.999 | 1.002 | 0.998 |
| 0.1 | 0.9 | 100.0% | -0.999 | 1.003 | 0.999 | 1.002 | 0.998 |
| 0.2 | 0.8 | 100.0% | -1.000 | 1.003 | 0.999 | 1.002 | 0.998 |
| 0.3 | 0.7 | 100.0% | -1.000 | 1.002 | 1.000 | 1.002 | 0.998 |
| 0.4 | 0.6 | 100.0% | -1.001 | 1.002 | 1.000 | 1.001 | 0.999 |
| 0.5 | 0.5 | 100.0% | -1.001 | 1.001 | 1.000 | 1.001 | 0.999 |
| 0.6 | 0.4 | 100.0% | -1.001 | 1.001 | 1.000 | 1.000 | 1.000 |
| 0.7 | 0.3 | 100.0% | -1.001 | 1.001 | 1.001 | 1.000 | 1.000 |
| 0.8 | 0.2 | 100.0% | -1.001 | 1.001 | 1.001 | 1.000 | 1.000 |
| 0.9 | 0.1 | 100.0% | -1.001 | 1.001 | 1.001 | 1.000 | 1.000 |
| 1 | 0 | 100.0% | -1.001 | 1.001 | 1.001 | 1.000 | 1.000 |

## Validation, synthetically perturbed fields (not used for selection)

| α | β | Reached | Mean return | T/T_ref | E/E_ref | Optimal T/T_ref | Optimal E/E_ref |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 1 | 99.3% | -1.002 | 1.021 | 0.982 | 1.025 | 0.981 |
| 0.1 | 0.9 | 98.7% | -1.027 | 1.019 | 0.983 | 1.019 | 0.981 |
| 0.2 | 0.8 | 98.7% | -1.032 | 1.016 | 0.985 | 1.013 | 0.982 |
| 0.3 | 0.7 | 98.7% | -1.035 | 1.012 | 0.987 | 1.010 | 0.983 |
| 0.4 | 0.6 | 98.0% | -1.058 | 1.005 | 0.993 | 1.010 | 0.983 |
| 0.5 | 0.5 | 97.3% | -1.079 | 1.004 | 0.994 | 1.005 | 0.987 |
| 0.6 | 0.4 | 97.3% | -1.080 | 1.003 | 0.995 | 1.004 | 0.988 |
| 0.7 | 0.3 | 98.7% | -1.041 | 1.003 | 0.996 | 1.001 | 0.993 |
| 0.8 | 0.2 | 98.0% | -1.061 | 1.002 | 0.997 | 1.000 | 0.996 |
| 0.9 | 0.1 | 98.7% | -1.041 | 1.002 | 0.998 | 1.000 | 0.998 |
| 1 | 0 | 99.3% | -1.021 | 1.001 | 0.999 | 1.000 | 1.000 |

## Compared with the regret prior

The prior is greedy on alpha * time regret + beta * exposure regret, with no learned part: it uses the two cost-to-go searches alone. The difference is what training contributed.

Real fields:

| α | Policy reached | Prior reached | Policy return | Prior return | Policy T/T_ref, E/E_ref | Prior T/T_ref, E/E_ref |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 100.0% | 100.0% | -0.999 | -0.998 | 1.003, 0.999 | 1.002, 0.998 |
| 0.5 | 100.0% | 99.3% | -1.001 | -1.020 | 1.001, 1.000 | 1.000, 1.000 |
| 1 | 100.0% | 100.0% | -1.001 | -1.000 | 1.001, 1.001 | 1.000, 1.000 |

Perturbed fields:

| α | Policy reached | Prior reached | Policy return | Prior return | Policy T/T_ref, E/E_ref | Prior T/T_ref, E/E_ref |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 99.3% | 100.0% | -1.002 | -0.981 | 1.021, 0.982 | 1.025, 0.981 |
| 0.5 | 97.3% | 94.0% | -1.079 | -1.180 | 1.004, 0.994 | 1.004, 0.996 |
| 1 | 99.3% | 100.0% | -1.021 | -1.000 | 1.001, 0.999 | 1.000, 1.000 |
