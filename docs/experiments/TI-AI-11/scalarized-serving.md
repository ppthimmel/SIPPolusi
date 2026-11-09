# Scalarized route-policy package

The selected PIC A checkpoint is a preference-conditioned route policy. Its selected
checkpoint is the warm start (update 0); the evaluation outcome is conditional, and it
does not establish exposure savings or an advantage over weighted search. See the
paired comparison in `morl-comparison/`.

Create a package from the repository root (requires the Backend runtime dependencies,
including CPU PyTorch):

```powershell
python scripts/package_scalarized_policy.py --run docs/experiments/TI-AI-09/scalarized/20261103T081938Z-scalarized-bike-v1-6eab87d4 --out src/backend/route_model/runs/scalarized-package --decision conditional_candidate
```

The destination must be empty. The packager verifies the selected checkpoint against
the training manifest, preserves reproducibility metadata without machine-local paths,
and hashes every included artifact. The family is `preference_conditioned` and the
registered model name remains `morl-route-policy`. Its Backend loader validates hashes
and the data-contract version, checks the graph per request, and supplies the requested
preference directly. It uses the same timeout, cancellation, and marked fallback path as
the fixed-preference family. `member_alpha` is null because there is no discrete member.

To register through the private development registry, stage the package in the bucket
and follow `scripts/railway/README.md`. Set `REGISTER_ALIASES` to an empty string if the
new version should be registered without moving any existing alias. Do not assign the
`production` alias until a Backend containing the new loader has been deployed and the
team has decided to switch serving. A registry entry alone does not deploy code.

Local tests compare the original and packaged checkpoint weights and routes for several
preferences, exercise integrity and compatibility rejection, and exercise the Backend
loader and fallback. They do not constitute a fresh-environment or live Railway test.

## Development staging (9 November 2026)

The selected checkpoint was packaged with data contract `1.1.0` and decision `conditional_candidate`. The package SHA-256 is `564c693081968cd1750c7a996b5301e34148200b21cc8fde7c5f6f1b626fed52`. It was uploaded to the development bucket at `packaged/route/scalarized/20261103T081938Z-scalarized-bike-v1-6eab87d4-564c69308196/` and all three files were downloaded and checksum-verified.

## Development registry verification (9 November 2026)

The previously removed `model_registry` service was temporarily restored for registration. The package was registered through the MLflow artifact proxy as `morl-route-policy` **version 2**, run `40edd2f24cf44b30987fc96f8be4b03f`, with no aliases assigned. The registration script downloaded all three staged files, verified the package, and then resolved the registered version and verified both listed artifact hashes and the package manifest hash. Its output ended:

```text
registered: morl-route-policy version 2 aliases (none)
RESOLVED morl-route-policy:2 all 2 files match.  RESULT: PASS
```

An independent registry read showed version 2 in `READY` state with `package_sha256` `564c693081968cd1750c7a996b5301e34148200b21cc8fde7c5f6f1b626fed52`. Both `production` and `candidate` still pointed to version 1. Version 2 has not been promoted, and the new Backend loader has not been deployed.

The temporary registry deployment `54de2fec-ea6f-480c-99dc-2c0b2e37b39f` was removed after verification. Railway reported **zero active `model_registry` deployments**. An initial attempt to redeploy the old stored snapshot failed because it did not contain `src/model_registry`; the successful temporary deployment was rebuilt from the service's configured repository source.
