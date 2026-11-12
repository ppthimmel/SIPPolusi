"""Download a registered route policy package from the model registry (TI-SE-07b).

The registry is MLflow behind private networking with its artifact proxy on
(--serve-artifacts), so the Backend needs MLFLOW_TRACKING_URI only: no S3
credentials and no mlflow package. Three REST calls:

    GET /api/2.0/mlflow/registered-models/alias?name=&alias=   (or model-versions/get?name=&version=)
    GET /api/2.0/mlflow-artifacts/artifacts?path=<dir>          list one directory
    GET /api/2.0/mlflow-artifacts/artifacts/<file>              one file

A version must carry the package_sha256 tag the registration wrote. The package is
downloaded into a temporary directory, checked (every file hash, no unlisted file,
SHA-256 of package.json equal to the tag) and only then moved to
<cache>/<name>/<version>/<package_sha256>, so the cache never holds a partial or
altered package. A cached package is checked again before use.

Timeouts and connection errors are retried a bounded number of times (the registry
sleeps when idle and takes 10-30 s to wake), and so are 408 and 429; other 4xx answers are
not, and are marked retryable=False so the caller does not ask again. A `stop` event (set at shutdown) ends
the retries and their waits at once.

One 4xx is told apart (TI-SE-07c): the version lookup saying the model, the alias or the version does
not exist (yet) raises ModelNotRegistered. That one changes by itself once the model is registered, so
the solver asks again later instead of stopping. MLflow 2.17.2 answers it as
    alias      400 INVALID_PARAMETER_VALUE "Registered model alias <alias> not found."
               (also when the model itself is not registered)
    version    404 RESOURCE_DOES_NOT_EXIST "Model Version (name=..., version=...) not found"
               or "Registered Model with name=... not found"
Every other 4xx, a 4xx without MLflow's error body (a proxy, a wrong URL) and a 404 while downloading
the artifacts of a registered version stay plain RegistryErrors.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from route_model.model_package import PackageError, package_digest, validate_package

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACTS = "/api/2.0/mlflow-artifacts/artifacts"
# Client errors that say "later", not "never".
_TEMPORARY = (408, 429)


class RegistryError(Exception):
    """The registry could not be reached, or did not hold a usable version. ``retryable`` says whether
    asking again can help (timeouts, 5xx), or the registry's answer has to change first (4xx, a missing
    tag, artifacts outside the proxy)."""

    def __init__(self, message: str, retryable: bool = True, *, status: int | None = None,
                 error_code: str | None = None, detail: str = ""):
        super().__init__(message)
        self.retryable = retryable
        self.status, self.error_code, self.detail = status, error_code, detail  # an HTTP answer, if any


class ModelNotRegistered(RegistryError):
    """The registry holds no version for name@selector yet: no such model, alias or version. Not retried
    within the call (retryable=False); the solver checks again every model_recheck_s, since registering
    the model fixes it without a redeploy."""

    def __init__(self, message: str, **kw):
        super().__init__(message, retryable=False, **kw)


_ALIAS_NOT_FOUND = re.compile(r"\balias\b.*\bnot found\b", re.IGNORECASE)


def _not_registered(error: RegistryError, by_alias: bool) -> bool:
    if error.status == 404 and error.error_code == "RESOURCE_DOES_NOT_EXIST":
        return True
    # MLflow answers an alias that is not set (model registered or not) with 400, not 404.
    return (by_alias and error.status == 400 and error.error_code == "INVALID_PARAMETER_VALUE"
            and bool(_ALIAS_NOT_FOUND.search(error.detail)))


def _error_body(response: httpx.Response) -> tuple[str | None, str]:
    """MLflow's {"error_code", "message"}, or (None, "") when the answer is not one."""
    try:
        body = response.json()
    except ValueError:
        return None, ""
    if not isinstance(body, dict):
        return None, ""
    code = body.get("error_code")
    return (code if isinstance(code, str) else None), str(body.get("message") or "")


@dataclass(frozen=True)
class ResolvedPackage:
    directory: Path
    name: str
    version: str
    package_sha256: str

    @property
    def identity(self) -> str:
        return f"{self.name}:{self.version}"


class RegistryClient:
    def __init__(self, base_url: str, *, timeout_s: float = 30.0, attempts: int = 3, backoff_s: float = 2.0,
                 transport: httpx.BaseTransport | None = None, stop: threading.Event | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.attempts = max(1, attempts)
        self.backoff_s = backoff_s
        self._transport = transport  # tests inject httpx.MockTransport
        self._stop = stop or threading.Event()

    def _get(self, client: httpx.Client, url: str, **params) -> httpx.Response:
        last = None
        for attempt in range(self.attempts):
            if self._stop.is_set():
                raise RegistryError("stopped (shutting down)")
            try:
                response = client.get(url, params=params or None)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = RegistryError(f"registry unreachable ({type(exc).__name__})")
            else:
                if response.status_code == 200:
                    return response
                if response.status_code < 500 and response.status_code not in _TEMPORARY:
                    code, detail = _error_body(response)
                    raise RegistryError(f"registry answered HTTP {response.status_code}"
                                        f"{f' {code}' if code else ''} for {url}", retryable=False,
                                        status=response.status_code, error_code=code, detail=detail)
                last = RegistryError(f"registry answered HTTP {response.status_code}")
            if attempt + 1 < self.attempts and self._stop.wait(self.backoff_s * (2 ** attempt)):
                raise RegistryError("stopped (shutting down)")
        raise last

    def _version(self, client: httpx.Client, name: str, selector: str) -> dict:
        by_alias = not str(selector).isdigit()
        try:
            if by_alias:
                body = self._get(client, "/api/2.0/mlflow/registered-models/alias", name=name, alias=selector).json()
            else:
                body = self._get(client, "/api/2.0/mlflow/model-versions/get", name=name, version=str(selector)).json()
        except RegistryError as exc:
            if _not_registered(exc, by_alias):
                raise ModelNotRegistered(f"{name}@{selector} is not registered (HTTP {exc.status} {exc.error_code}: "
                                         f"{exc.detail})", status=exc.status, error_code=exc.error_code,
                                         detail=exc.detail) from exc
            raise
        version = body.get("model_version")
        if not isinstance(version, dict) or not version.get("version") or not version.get("source"):
            raise RegistryError(f"registry returned no model version for {name}@{selector}", retryable=False)
        return version

    def _download(self, client: httpx.Client, remote: str, local: Path) -> int:
        """Copy the artifact directory `remote` into `local`; returns the number of files."""
        listing = self._get(client, _ARTIFACTS, path=remote).json().get("files", [])
        count = 0
        for item in listing:
            name = item.get("path", "")
            # The proxy lists base names; anything else would let the registry write outside `local`.
            if not name or "/" in name or name in (".", ".."):
                raise RegistryError(f"registry listed an unsafe artifact name {name!r}", retryable=False)
            if item.get("is_dir"):
                (local / name).mkdir()
                count += self._download(client, f"{remote}/{name}", local / name)
                continue
            response = self._get(client, f"{_ARTIFACTS}/{quote(f'{remote}/{name}')}")
            (local / name).write_bytes(response.content)
            count += 1
        return count

    def resolve(self, name: str, selector: str, cache_dir: str | os.PathLike) -> ResolvedPackage:
        """Download (or reuse from the cache) and verify the package registered as name@selector."""
        with httpx.Client(base_url=self.base_url, timeout=self.timeout_s, transport=self._transport) as client:
            version = self._version(client, name, selector)
            tags = {t.get("key"): t.get("value") for t in version.get("tags", []) if isinstance(t, dict)}
            expected = tags.get("package_sha256") or ""
            if not _SHA256.match(expected):
                raise RegistryError(f"{name}:{version['version']} has no package_sha256 tag", retryable=False)
            source = str(version["source"])
            if not source.startswith("mlflow-artifacts:"):
                raise RegistryError(f"{name}:{version['version']} is not stored behind the artifact proxy "
                                    f"(source scheme {source.split(':', 1)[0]!r})", retryable=False)
            # mlflow-artifacts:/2/<run>/artifacts/package, or with a host: mlflow-artifacts://host/2/...
            remote = re.sub(r"^mlflow-artifacts:(//[^/]+)?/*", "", source).rstrip("/")
            resolved = ResolvedPackage(Path(cache_dir) / name / str(version["version"]) / expected, name,
                                       str(version["version"]), expected)
            if resolved.directory.exists():
                try:
                    _verify(resolved.directory, expected)
                    return resolved
                except ValueError:
                    # Altered, truncated or unreadable on disk (PackageError, JSONDecodeError and
                    # UnicodeDecodeError are all ValueError): discard it and download it again now.
                    shutil.rmtree(resolved.directory, ignore_errors=True)
            resolved.directory.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=resolved.directory.parent) as tmp:
                staging = Path(tmp) / "package"
                staging.mkdir()
                self._download(client, remote, staging)
                _verify(staging, expected)  # a bad download is the registry's package: not retried
                os.replace(staging, resolved.directory)
        return resolved


def _verify(directory: Path, expected_sha256: str) -> None:
    validate_package(directory, "route_policy")
    if package_digest(directory) != expected_sha256:
        raise PackageError("package.json does not match the registered package_sha256")
