# 20261103T125951Z-scalarized-bike-mlp-only-v1-ec52d9f2

- **Status:** `interrupted`: Stopped on request after update 60, to avoid further training for TI-AI-10; the process was killed, so updates after the last checkpoint are lost and final_evaluation did not run
- **Algorithm:** `ppo-scalarized-1`; seed 42; 87,042 parameters; config SHA-256 `ec52d9f2344d`
- **Code:** commit `c3ca3366bfe6d023fa810744243cd56665da744d`, uncommitted changes: False
- **Graph:** `jakarta-20261005`, bike; fields manifest SHA-256 `7329a154a9fa`; OD set SHA-256 `a420b08fd3b7`
- **Progress:** 60 updates, 122,880 environment steps, 1,658 episodes
- **Selection:** validation mean_return (greedy policy; failure = -failure_return), split `val`, α ∈ [0.0, 0.25, 0.5, 0.75, 1.0]; test split loaded: False
- **Selected checkpoint:** update 60, validation return -1.0652, reached 98.1%, SHA-256 `2e21da1df561`
