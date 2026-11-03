"""Response schemas of the public API (TI-SE-08), used for the OpenAPI document.

The endpoints build their GeoJSON as dicts (solver.geojson, public_api.surface).
These models describe that output with units, and extra="forbid" makes the
contract tests fail when a response carries a field documented nowhere.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from osm.models import Mode
from osm.schemas import LonLat
from planner.schemas import PreferenceOut
from pollution.schemas import HourlyTimestamp

_STRICT = ConfigDict(extra="forbid", allow_inf_nan=False)


# ------------------------------------------------------------------ errors (Tabel 3.16)

class ErrorDetail(BaseModel):
    model_config = _STRICT

    code: str = Field(description="Kode galat Tabel 3.16, mis. invalid_request, no_route_found, cache_unavailable")
    message: str = Field(description="Penjelasan singkat yang dapat ditampilkan ke pengguna")
    request_id: str = Field(description="Sama dengan header X-Request-ID, untuk menelusuri log")
    reason: str | None = Field(None, description="Penyebab lebih rinci bila ada, mis. cache_empty, solver_timeout")


class ErrorResponse(BaseModel):
    model_config = _STRICT

    error: ErrorDetail


# ------------------------------------------------------------------ POST /v1/routes (Kode 3.2)

class LineString(BaseModel):
    model_config = _STRICT

    type: Literal["LineString"]
    coordinates: list[LonLat] = Field(min_length=2, description="[lon, lat] WGS 84 (CRS84), searah perjalanan")


class RouteProperties(BaseModel):
    model_config = _STRICT

    role: Literal["recommended", "comparison"] = Field(
        description="recommended = rute rekomendasi; comparison = rute tercepat sebagai pembanding")
    route_source: Literal["policy", "baseline"] = Field(
        description="policy = hasil Multi-Objective Route Model; baseline = rute tercepat (A*)")
    preference_applied: bool = Field(description="Apakah preferensi α/β diterapkan; selalu false untuk baseline")
    travel_time_min: float = Field(ge=0, description="T(R), menit")
    distance_m: float = Field(ge=0, description="Panjang rute, meter")
    exposure_index_min: float = Field(ge=0, description="E(R) = Σ Iₑ·τₑ, indeks·menit")
    exposure_ug_min_m3: float | None = Field(
        ge=0, description="Σ konsentrasi polutan acuan · τₑ, µg·min/m³; null bila ada ruas tanpa konsentrasi")
    concentration_coverage: float = Field(ge=0, le=1, description="Bagian ruas yang memiliki konsentrasi, 0–1")
    confidence_score: float = Field(ge=0, le=1, description="Rata-rata confidence ruas berbobot paparan, 0–1")
    imputed_edge_count: int = Field(ge=0, description="Jumlah ruas yang indeksnya diisi median wilayah")
    edge_ids: list[int] = Field(description="Urutan edge_id OSM Cache DB dari asal ke tujuan")


class RouteFeature(BaseModel):
    model_config = _STRICT

    type: Literal["Feature"]
    geometry: LineString
    properties: RouteProperties


class ModelVersion(BaseModel):
    model_config = _STRICT

    downscaling: str | None = Field(description="Versi Spatial Downscaling Model untuk time window yang dipakai")
    route_policy: str | None = Field(description="Versi Route Model, atau astar-baseline saat fallback")


class Estimation(BaseModel):
    model_config = _STRICT

    source: str | None = Field(description=(
        "Sumber estimasi polusi pada ruas rute: idw = interpolasi sensor darat saja; stgnn atau hybrid = model "
        "downscaling ikut dipakai; mixed = ruas berbeda sumber; null = tidak tercatat di cache"))
    fallback_pollutants: list[Literal["pm25", "no2"]] = Field(description=(
        "Polutan yang estimasinya berasal dari fallback IDW karena model downscaling terdaftar tidak dapat dipakai"))


class ComparisonSummary(BaseModel):
    model_config = _STRICT

    delta_travel_time_min: float = Field(description="T(rekomendasi) − T(pembanding), menit")
    delta_exposure_pct: float | None = Field(description="Perubahan E(R) rekomendasi terhadap pembanding, %")


class RouteMetadata(BaseModel):
    model_config = _STRICT

    request_id: str
    mode: Mode
    pollutant: Literal["pm25", "no2"] = Field(description="Polutan acuan untuk exposure_ug_min_m3")
    preference: PreferenceOut
    route_source: Literal["policy", "baseline"]
    preference_applied: bool
    fallback_reason: str | None = Field(description=(
        "null bila rute rekomendasi dari model. Saat fallback: model_timeout, model_unavailable, model_error, "
        "model_invalid_response, model_no_route; policy_unavailable bila penyedianya baseline"))
    time_window_start: HourlyTimestamp = Field(description="Awal time window polusi (ISO 8601, UTC)")
    data_stale: bool = Field(description="true bila time window terakhir berakhir lebih dari 3 jam lalu")
    missing_edge_count: int = Field(description="Ruas subgraf tanpa estimasi polusi")
    graph_version: str = Field(description="Versi road graph OSM Cache DB")
    model_version: ModelVersion
    estimation: Estimation
    solver: str = Field(description="Penyedia yang menghasilkan rute: mock, baseline, atau http")
    comparison: ComparisonSummary
    notes: list[str] = Field(description="Keterangan untuk pengguna: fallback, data kedaluwarsa, ruas tanpa estimasi")
    crs: Literal["urn:ogc:def:crs:OGC::CRS84"] = Field(description="Sistem acuan koordinat (RFC 7946: WGS 84)")
    coordinate_order: list[Literal["lon", "lat"]] = Field(description="Urutan sumbu koordinat: [lon, lat]")
    units: dict[str, str] = Field(description="Satuan setiap metrik numerik pada properties dan metadata.comparison")
    exposure_index_definition: str
    disclaimer: str


class RouteFeatureCollection(BaseModel):
    """GeoJSON FeatureCollection (RFC 7946, Kode 3.2): Feature recommended lalu comparison."""

    model_config = _STRICT

    type: Literal["FeatureCollection"]
    features: list[RouteFeature] = Field(min_length=2, max_length=2)
    metadata: RouteMetadata

    @model_validator(mode="after")
    def validate_roles(self) -> RouteFeatureCollection:
        if [f.properties.role for f in self.features] != ["recommended", "comparison"]:
            raise ValueError("RouteOutput requires recommended then comparison")
        return self


# ------------------------------------------------------------------ GET /v1/exposure-surface

class Polygon(BaseModel):
    model_config = _STRICT

    type: Literal["Polygon"]
    coordinates: list[list[LonLat]] = Field(description="Satu ring tertutup [lon, lat] WGS 84 (CRS84)")


class EdgeSurfaceProperties(BaseModel):
    """zoom ≥ 14: nilai satu ruas."""

    model_config = _STRICT

    edge_id: int
    exposure_index: float | None = Field(description="Indeks paparan gabungan Iₑ,ₜ, tanpa satuan")
    pm25_ugm3: float | None = Field(description="PM2.5, µg/m³; null bila tidak tersedia")
    no2_ugm3: float | None = Field(description="NO2, µg/m³; null bila tidak tersedia")
    confidence_score: float | None = Field(description="0–1")
    estimation_source: str | None = Field(description="stgnn atau idw")


class CellSurfaceProperties(BaseModel):
    """zoom < 14: agregat ruas dalam satu sel grid."""

    model_config = _STRICT

    cell_id: str = Field(description="<ukuran>m:<ix>:<iy>, tetap saat peta digeser")
    edge_count: int = Field(description="Jumlah ruas dalam sel")
    exposure_index: float | None = Field(description="Rata-rata berbobot panjang ruas, tanpa satuan")
    exposure_index_max: float | None = Field(description="Indeks ruas tertinggi dalam sel")
    pm25_ugm3: float | None = Field(description="Rata-rata berbobot panjang, µg/m³")
    no2_ugm3: float | None = Field(description="Rata-rata berbobot panjang, µg/m³")
    confidence_score: float | None = Field(description="Rata-rata berbobot panjang, 0–1")


class EdgeSurfaceFeature(BaseModel):
    model_config = _STRICT

    type: Literal["Feature"]
    geometry: LineString
    properties: EdgeSurfaceProperties


class CellSurfaceFeature(BaseModel):
    model_config = _STRICT

    type: Literal["Feature"]
    geometry: Polygon
    properties: CellSurfaceProperties


class SurfaceModelVersion(BaseModel):
    model_config = _STRICT

    downscaling: str | None


class SurfaceMetadata(BaseModel):
    """Tanpa request_id: body yang sama dapat dipakai ulang dari cache untuk permintaan lain
    (ID permintaan ada di header X-Request-ID)."""

    model_config = _STRICT

    kind: Literal["edge", "grid"] = Field(description="edge untuk zoom ≥ 14, grid untuk zoom < 14")
    zoom: int
    layer: Literal["combined", "pm25", "no2"] = Field(
        description="Lapisan yang diminta; semua nilai tetap dikirim sehingga lapisan dapat diganti tanpa request baru")
    bbox: list[float] = Field(description="bbox yang diminta: min_lon, min_lat, max_lon, max_lat")
    cell_size_m: int | None = Field(description="Ukuran sel grid, meter (250, 500, atau 1000); null untuk kind edge")
    aggregation: str | None = Field(description="Cara agregasi sel; null untuk kind edge")
    feature_count: int
    time_window_start: str = Field(description="ISO 8601, UTC")
    time_window_end: str = Field(description="ISO 8601, UTC")
    data_stale: bool
    graph_version: str
    model_version: SurfaceModelVersion
    crs: Literal["urn:ogc:def:crs:OGC::CRS84"]
    coordinate_order: list[Literal["lon", "lat"]]
    units: dict[str, str] = Field(description="Satuan setiap properti numerik")
    exposure_index_definition: str
    exposure_index_classes: list[float] = Field(description="Batas kelas skala warna heatmap (Subbab 3.1)")
    disclaimer: str


class SurfaceFeatureCollection(BaseModel):
    """GeoJSON FeatureCollection (RFC 7946): ruas (LineString) atau sel grid (Polygon)."""

    model_config = _STRICT

    type: Literal["FeatureCollection"]
    features: list[EdgeSurfaceFeature] | list[CellSurfaceFeature]
    metadata: SurfaceMetadata
