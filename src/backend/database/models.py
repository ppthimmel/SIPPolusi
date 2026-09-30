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
    Index,
    Integer,
    MetaData,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import REAL
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
        CheckConstraint("estimation_source IN ('stgnn', 'idw')", name="ck_edge_pollution_estimation_source"),
    )

    edge_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    time_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    pm25_ugm3: Mapped[float | None] = mapped_column(REAL)
    no2_ugm3: Mapped[float | None] = mapped_column(REAL)
    exposure_index: Mapped[float | None] = mapped_column(REAL)
    confidence_score: Mapped[float | None] = mapped_column(REAL)
    background_source: Mapped[str | None] = mapped_column(Text)
    estimation_source: Mapped[str | None] = mapped_column(Text)
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
