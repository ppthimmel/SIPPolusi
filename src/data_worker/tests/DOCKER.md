# Data Worker tests in Docker

From the repository root, build the reusable image and run the full suite:

```sh
docker compose -f docker-compose.data-worker-tests.yml run --build --rm tests
```

For later runs, omit `--build` unless the requirements or test Dockerfile change.
The current checkout is mounted into the container, so code changes are visible
without rebuilding. To run selected tests, override the default command, for example:

```sh
docker compose -f docker-compose.data-worker-tests.yml run --rm tests python -m pytest tests/test_training_matrix.py -p no:cacheprovider -ra
```

The Compose project starts a separate PostGIS database with disposable credentials,
without exposing a host port. The test container can write to the mounted checkout;
the test database is separate from application databases. When finished, stop and
remove the test services with:

```sh
docker compose -f docker-compose.data-worker-tests.yml down
```

The image remains available for future runs. Building requires access to Docker Hub,
Debian, the PyTorch CPU wheel repository, and PyPI.
