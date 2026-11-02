"""ORM models for the shared PostgreSQL/PostGIS instance (subbab 3.5, Tabel 3.17).

Two cache schemas live on one instance: `pollution` (Spatial Pollution Cache DB)
and `osm` (OSM Cache DB). The `mlflow` schema is owned by MLflow itself and only
reserved here. Data Worker writes these tables; Backend reads them. This file is
kept identical in src/backend/database and src/data_worker/database because each
service is built from its own Docker context.
"""
from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import REAL, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMAS = ("pollution", "osm", "mlflow")


class PollutionBase(DeclarativeBase):
    metadata = MetaData(schema="pollution")


class OsmBase(DeclarativeBase):
    metadata = MetaData(schema="osm")


class EdgePollution(PollutionBase):
    __tablename__ = "edge_pollution"
    __table_args__ = (
        CheckConstraint("background_source IN ('aod', 'reanalysis')", name="ck_edge_pollution_background_source"),
        CheckConstraint("estimation_source IN ('stgnn', 'idw', 'hybrid')", name="ck_edge_pollution_estimation_source"),
    )

    edge_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    time_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    pm25_ugm3: Mapped[float | None] = mapped_column(REAL)
    no2_ugm3: Mapped[float | None] = mapped_column(REAL)
    exposure_index: Mapped[float | None] = mapped_column(REAL)
    confidence_score: Mapped[float | None] = mapped_column(REAL)
    background_source: Mapped[str | None] = mapped_column(Text)
    estimation_source: Mapped[str | None] = mapped_column(Text)
    # Rows written before metadata_id existed keep their own copy here; newer rows
    # leave it '{}' and point at the run's shared value through metadata_id.
    model_metadata: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    metadata_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("pollution.edge_metadata.metadata_id"))
    trace_run_id: Mapped[str | None] = mapped_column(Text, ForeignKey("pollution.inference_run.run_id"))
    graph_version: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str | None] = mapped_column(Text)
    geom = mapped_column(Geometry("LINESTRING", srid=4326))  # GiST index created by GeoAlchemy2
    written_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PollutionWindow(PollutionBase):
    __tablename__ = "pollution_window"
    __table_args__ = (CheckConstraint("status IN ('writing', 'complete')", name="ck_pollution_window_status"),)

    time_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    pollutant: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    coverage_ratio: Mapped[float | None] = mapped_column(REAL)
    model_version: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InferenceRun(PollutionBase):
    __tablename__ = "inference_run"
    __table_args__ = (Index("inference_run_window_idx", "time_window_start"),)
    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    time_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True),
        ForeignKey("pollution.pollution_window.time_window_start", ondelete="CASCADE"), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trace_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    # NULL for runs whose road-cell pieces are in the legacy edge_cell table.
    edge_cell_set_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("pollution.edge_cell_set.set_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EdgeMetadata(PollutionBase):
    """The distinct per-edge model_metadata values of one run, stored once each."""
    __tablename__ = "edge_metadata"
    __table_args__ = (UniqueConstraint("run_id", "model_metadata", name="edge_metadata_run_value_key"),)
    metadata_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[str] = mapped_column(Text, ForeignKey("pollution.inference_run.run_id", ondelete="CASCADE"),
                                        nullable=False)
    model_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False)


class EdgeCellSet(PollutionBase):
    """One road-to-grid mapping, identified by the pieces_sha256 in the run manifest."""
    __tablename__ = "edge_cell_set"
    set_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    pieces_sha256: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    piece_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EdgeCellPiece(PollutionBase):
    __tablename__ = "edge_cell_piece"
    __table_args__ = (CheckConstraint("length_m > 0", name="ck_edge_cell_piece_length"),)
    set_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("pollution.edge_cell_set.set_id", ondelete="CASCADE"),
                                        primary_key=True)
    edge_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cell_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    length_m: Mapped[float] = mapped_column(Double, nullable=False)


class GridPrediction(PollutionBase):
    __tablename__ = "grid_prediction"
    run_id: Mapped[str] = mapped_column(Text, ForeignKey("pollution.inference_run.run_id", ondelete="CASCADE"), primary_key=True)
    cell_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    x: Mapped[float] = mapped_column(Double)
    y: Mapped[float] = mapped_column(Double)
    pm25_ugm3: Mapped[float | None] = mapped_column(Double)
    no2_ugm3: Mapped[float | None] = mapped_column(Double)
    confidence_score: Mapped[float | None] = mapped_column(Double)
    pm25_source: Mapped[str] = mapped_column(Text)
    no2_source: Mapped[str] = mapped_column(Text)


class EdgeCell(PollutionBase):
    """Legacy road-cell pieces, one copy per run. No longer written (see EdgeCellPiece)
    and read only for runs with edge_cell_set_id NULL; empties as their windows are pruned."""
    __tablename__ = "edge_cell"
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "cell_id"], ["pollution.grid_prediction.run_id", "pollution.grid_prediction.cell_id"], ondelete="CASCADE"),
        CheckConstraint("length_m > 0", name="ck_edge_cell_length"),
    )
    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    edge_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cell_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    length_m: Mapped[float] = mapped_column(Double)


class RoadNode(OsmBase):
    __tablename__ = "road_node"

    node_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    graph_version: Mapped[str] = mapped_column(Text, primary_key=True)
    geom = mapped_column(Geometry("POINT", srid=4326))


class RoadEdge(OsmBase):
    __tablename__ = "road_edge"

    edge_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    graph_version: Mapped[str] = mapped_column(Text, primary_key=True)
    u: Mapped[int] = mapped_column(BigInteger)
    v: Mapped[int] = mapped_column(BigInteger)
    length_m: Mapped[float | None] = mapped_column(REAL)
    highway: Mapped[str | None] = mapped_column(Text)
    walk_allowed: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    bike_allowed: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    geom = mapped_column(Geometry("LINESTRING", srid=4326))


class GraphVersion(OsmBase):
    __tablename__ = "graph_version"
    __table_args__ = (
        CheckConstraint("status IN ('building', 'active', 'retired')", name="ck_graph_version_status"),
        # "Paling banyak satu baris berstatus active"
        Index(
            "graph_version_single_active_idx",
            "status",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    version: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text)
    osm_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    built_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    node_count: Mapped[int | None] = mapped_column(Integer)
    edge_count: Mapped[int | None] = mapped_column(Integer)
