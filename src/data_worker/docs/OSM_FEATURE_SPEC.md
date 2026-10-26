# DKI Jakarta Road Network: CSV Export

This folder contains the full contents of the `osm` schema from the `railway` PostgreSQL database
(Railway project `humble-endurance`), exported to CSV on 2026-10-06.

It is a **road network of DKI Jakarta** (including Kepulauan Seribu) built from OpenStreetMap,
prepared for walking/cycling route planning and for attaching air-pollution values to each road segment.

| | |
|---|---|
| Source | OpenStreetMap, downloaded via the Overpass API |
| Area | DKI Jakarta, OSM relation [6362934](https://www.openstreetmap.org/relation/6362934) |
| Data as of | 2026-10-05 09:50:56 UTC |
| Map version | `jakarta-20261005` |
| Coordinates | WGS 84 longitude / latitude (EPSG:4326), the same as GPS |
| License | © OpenStreetMap contributors, [ODbL](https://www.openstreetmap.org/copyright). Attribution is required when the data is shared or shown on a map. |

---

## Files

| File | What it contains | Rows | Size |
|---|---|---|---|
| `osm_schema.csv` | Description of every column in the three database tables | 23 | 1.4 KB |
| `graph_version.csv` | Map versions that have been loaded | 1 | 151 B |
| `road_node.csv.gz` | Points of the network: intersections, road ends, and special points (gates, traffic lights, crossings) | 299,067 | 3.7 MB |
| `road_edge.csv.gz` | Road segments between two points, with shape, length, road type and permissions | 373,352 | 19 MB |

The two road tables are stored as gzip-compressed CSV to keep the repository small. The uncompressed
`road_node.csv` and `road_edge.csv` files are ignored by Git. To extract one, run
`gzip -dk road_node.csv.gz` or use any archive utility. The route loader reads the `.gz` files directly.

All CSV files are UTF-8, comma-separated, with a header row. Text containing commas or quotes is wrapped
in double quotes, and a quote inside text is written as two quotes (`""`), which is standard CSV.

### How the files relate

```
graph_version.csv           road_node.csv.gz              road_edge.csv.gz
-----------------           -------------                 -------------
version  <──────────────────  graph_version   ┌──────────── u   (start point)
                              node_id  <──────┤
                                              └──────────── v   (end point)
version  <────────────────────────────────────────────────── graph_version
```

- Every point and every segment belongs to one map version (`graph_version`).
- Each segment goes from point `u` to point `v`. Both are `node_id` values in `road_node.csv`
  (match on `node_id` **and** `graph_version`).
- Segments that share a point are connected. Following segments from point to point is how a route is built.

---

## `osm_schema.csv`: structure of the tables

One row per database column. Use it as a quick reference for the three tables below.

| Column | Meaning |
|---|---|
| `table_schema` | Database section the table is in (always `osm`) |
| `table_name` | Table name: `graph_version`, `road_node` or `road_edge` |
| `ordinal_position` | Column order in the table (1 = first) |
| `column_name` | Column name |
| `data_type` | PostgreSQL data type (see [Data types](#data-types) below) |
| `is_nullable` | `YES` if the column can be empty, `NO` if it must always have a value |
| `column_default` | Value used when none is given, e.g. `true`, `false`, `'{}'::jsonb` (empty JSON) |
| `description` | Explanation stored in the database (only filled for some columns) |

---

## `graph_version.csv`: map versions

Each time the map is downloaded and loaded again, a new version is added. Only one version is in use at a time.

| Column | Type | Example | Meaning and usage |
|---|---|---|---|
| `version` | text | `jakarta-20261005` | Unique name of the version. Every row in the other two files refers to it. |
| `status` | text | `active` | `building`: being loaded. `active`: the version in use (only one at a time). `retired`: replaced by a newer one. Applications should use the `active` version. |
| `osm_timestamp` | date/time (UTC) | `2026-10-05 09:50:56+00` | How current the OpenStreetMap data is. Changes made on OpenStreetMap after this time are not included. |
| `built_at` | date/time (UTC) | `2026-10-05 10:58:14+00` | When this version finished loading into the database. |
| `node_count` | integer | `299067` | Number of rows for this version in `road_node.csv`. |
| `edge_count` | integer | `373352` | Number of rows for this version in `road_edge.csv`. |

---

## `road_node.csv`: points

The points where road segments start and end. A point is one of:

- an **intersection**, where two or more roads meet,
- a **road end**, such as a dead end,
- a **special point**, such as a gate, traffic light, crossing, speed bump, railway crossing or toll booth.

| Column | Type | Example | Meaning and usage |
|---|---|---|---|
| `node_id` | integer | `29897851` | OpenStreetMap's ID for this point. Can be viewed at `https://www.openstreetmap.org/node/<node_id>`. Used by `u` and `v` in `road_edge.csv`. |
| `graph_version` | text | `jakarta-20261005` | Map version this point belongs to. |
| `lon` | decimal | `106.5372648` | Longitude (east-west position). Jakarta is around 106.7 to 107.0. |
| `lat` | decimal | `-5.7014529` | Latitude (north-south position). Negative because Jakarta is south of the equator. Mainland Jakarta is around -6.1 to -6.4. |
| `tags` | JSON text | `{"highway": "traffic_signals"}` | OpenStreetMap details about the point. `{}` means a plain intersection or road end. Useful for avoiding gates, counting traffic lights, or finding crossings. |

**Most common point tags**

| Tag | Count | Meaning |
|---|---|---|
| `barrier=gate` | 23,223 | Gate, often a neighborhood (RT/RW) or housing-complex gate that may be closed at night |
| `barrier=lift_gate` | 11,049 | Boom gate, e.g. a parking or complex entrance |
| `barrier=swing_gate` | 6,584 | Swing gate |
| `highway=crossing` | 4,702 | Pedestrian crossing |
| `barrier=height_restrictor` | 684 | Height limit bar |
| `highway=traffic_signals` | 507 | Traffic lights |
| `barrier=sliding_gate` | 504 | Sliding gate |
| `railway=level_crossing` | 373 | Road and railway crossing |
| `traffic_calming=bump` / `rumble_strip` | 188 / 236 | Speed bump / rumble strip |
| `barrier=toll_booth` | 163 | Toll booth |

---

## `road_edge.csv`: road segments

Each row is a piece of road between two points. A long road is cut into several segments at every
intersection and special point. The segment keeps the road's real shape, including curves and loops.

| Column | Type | Example | Meaning and usage |
|---|---|---|---|
| `edge_id` | integer | `1` | Unique ID of the segment. Never reused, even across map versions. The `pollution.edge_pollution` table in the database uses it to attach pollution values to a segment. |
| `graph_version` | text | `jakarta-20261005` | Map version this segment belongs to. |
| `u` | integer | `29897871` | Start point (`node_id` in `road_node.csv`). |
| `v` | integer | `29897855` | End point (`node_id` in `road_node.csv`). If `u` equals `v`, the segment is a loop that starts and ends at the same point (154 segments). |
| `length_m` | decimal | `444.72995` | Length in meters, measured along the road's shape on the Earth's surface (not a straight line). Used for route distance and pollution exposure. |
| `highway` | text | `residential` | Road type (see [Road types](#road-types-highway)). |
| `walk_allowed` | `t` / `f` | `t` | `t` (true) if people can walk on it. Walking routes should only use `t` segments. See [rules](#how-walk_allowed-and-bike_allowed-were-decided). |
| `bike_allowed` | `t` / `f` | `f` | `t` (true) if people can cycle on it. Cycling routes should only use `t` segments. |
| `osm_way_id` | integer | `4700148` | ID of the original OpenStreetMap road this segment was cut from. Several segments share it. Can be viewed at `https://www.openstreetmap.org/way/<osm_way_id>`. |
| `name` | text | `Jalan Pasar Senen` | Road name. Empty when the road has no name (about half of segments, mostly small lanes and paths). |
| `oneway` | `t` / `f` | `t` | `t` if vehicles, including bicycles, may only travel from `u` to `v`. The shape is stored in that direction. Cycling routes must follow it; walking routes can ignore it. Roundabouts are always one-way. |
| `tags` | JSON text | `{"highway": "primary", "lanes": "4", ...}` | **All** original OpenStreetMap details about the road (see [Common road tags](#common-road-tags-in-tags)). |
| `geom_wkt` | text (WKT) | `LINESTRING(106.535 -5.701, 106.534 -5.700, ...)` | The road's shape as a list of `longitude latitude` points, in order from `u` to `v`. This standard format ("Well-Known Text") opens directly in QGIS and most map tools. |

### Road types (`highway`)

| Value | Meaning | Segments | km |
|---|---|---|---|
| `residential` | Neighborhood street | 123,924 | 5,948 |
| `living_street` | Narrow residential lane (gang) | 91,046 | 4,067 |
| `service` | Access road: parking aisle, driveway, alley | 79,107 | 3,293 |
| `tertiary` | Local connecting road | 24,770 | 1,125 |
| `footway` | Footpath or sidewalk | 11,455 | 354 |
| `primary` | Main city road | 11,359 | 561 |
| `secondary` | District road | 10,874 | 449 |
| `trunk` | Major arterial road | 4,875 | 265 |
| `unclassified` | Minor road | 3,086 | 184 |
| `path` | Generic path | 2,906 | 157 |
| `busway` | TransJakarta bus lane | 1,964 | 255 |
| `steps` | Stairs | 1,651 | 31 |
| `motorway_link` | Toll road ramp | 1,359 | 178 |
| `motorway` | Toll road | 1,222 | 356 |
| `primary_link`, `trunk_link`, `secondary_link`, `tertiary_link` | Ramps and slip roads of those road types | 2,860 | 89 |
| `pedestrian` | Pedestrian street or plaza | 308 | 19 |
| `track` | Unpaved track | 287 | 37 |
| `cycleway` | Bike path | 222 | 19 |
| `corridor` | Indoor walkway | 77 | 1 |

Total: 373,352 segments, about 17,388 km.

### How `walk_allowed` and `bike_allowed` were decided

They are worked out from the OpenStreetMap tags. When a tag is missing, the road is assumed to have no restriction.

**Walking is NOT allowed when:**
- the road is a toll road (`motorway`, `motorway_link`, or tagged `toll=yes`) or a busway, or
- it is tagged `foot=no` or `foot=private`, or
- it is a `cycleway`, unless walking is explicitly allowed (`foot=yes`, `designated` or `permissive`), or
- it is private (`access=private` or `access=no`), unless walking is explicitly allowed.

**Cycling is NOT allowed when:**
- the road is a toll road or a busway, or
- it is tagged `bicycle=no`, `bicycle=private` or `bicycle=use_sidepath` (cyclists must use a separate bike path), or
- it is a `footway`, `pedestrian`, `steps` or `corridor`, unless cycling is explicitly allowed, or
- it is private, unless cycling is explicitly allowed.

Result: 315,665 walkable and 303,972 cyclable segments.

### Common road tags (in `tags`)

Each road keeps every tag it has on OpenStreetMap. Coverage varies: some tags are on most roads, others on very few.

| Tag | On % of roads | Example values | Meaning |
|---|---|---|---|
| `highway` | 100% | `residential`, `primary` | Road type (same as the `highway` column) |
| `surface` | 61% | `asphalt`, `concrete`, `paving_stones` | Road surface |
| `oneway` | 54% | `yes`, `no`, `-1` | One-way (already used for the `oneway` column) |
| `motorcycle` | 51% | `yes`, `no` | Motorcycles allowed |
| `lanes` | 49% | `2`, `4` | Number of lanes |
| `width` | 44% | `4`, `12` | Road width in meters |
| `smoothness` | 43% | `good`, `intermediate`, `bad` | Surface quality |
| `name` | 42% | `Jalan Kesenian` | Road name (same as the `name` column) |
| `access` | 23% | `private`, `destination` | Who may use the road |
| `bridge` | 3% | `yes` | Bridge or flyover |
| `layer` | 3% | `1`, `-1` | Vertical level (bridges above, tunnels below) |
| `sidewalk` | 1.6% | `both`, `left`, `no` | Whether there is a sidewalk |
| `maxspeed` | 1.4% | `40`, `60` | Speed limit in km/h |
| `toll` | 1.1% | `yes` | Toll road |
| `cycleway:left` | 0.3% | `lane`, `separate` | Bike lane on the left side |
| `tunnel` | 0.2% | `yes`, `building_passage` | Tunnel or passage under a building |
| `junction` | 0.2% | `roundabout`, `circular` | Part of a roundabout |

Full tag documentation: <https://wiki.openstreetmap.org/wiki/Map_features>

---

## Data types

| Type in `osm_schema.csv` | Meaning | How it appears in the CSV |
|---|---|---|
| `bigint`, `integer` | Whole number | `29897851` |
| `real` | Decimal number | `444.72995` |
| `text` | Text | `Jalan Pasar Senen` |
| `boolean` | True / false | `t` / `f` |
| `timestamp with time zone` | Date and time | `2026-10-05 09:50:56+00` (`+00` = UTC) |
| `jsonb` | Key/value details in JSON | `{"highway": "primary", "lanes": "4"}` |
| `geometry(Point,4326)` | A GPS point | Split into `lon` and `lat` columns in `road_node.csv` |
| `geometry(LineString,4326)` | A line of GPS points | `geom_wkt` column in `road_edge.csv` |

---

## Using the files

**Excel / Google Sheets:** extract the `.csv.gz` files before opening them. The extracted
`road_edge.csv` is large (136 MB), so it loads slowly.

**QGIS (map view):**
- Extract `road_node.csv.gz`, then use *Layer → Add Delimited Text Layer* and choose *Point coordinates* with X = `lon`, Y = `lat`, CRS `EPSG:4326`.
- Extract `road_edge.csv.gz`, then use the same menu and choose *Well-Known Text (WKT)* with geometry field `geom_wkt`, CRS `EPSG:4326`.

**Python (pandas):**
```python
import pandas as pd, json
edges = pd.read_csv("road_edge.csv.gz", true_values=["t"], false_values=["f"])
nodes = pd.read_csv("road_node.csv.gz")
edges["tags"] = edges["tags"].apply(json.loads)
edges["lanes"] = edges["tags"].apply(lambda t: t.get("lanes"))
```

**Back into PostgreSQL / PostGIS:** the database already has these tables in the `osm` schema. To load the
CSV elsewhere, rebuild the geometry with `ST_GeomFromText(geom_wkt, 4326)` for segments and
`ST_SetSRID(ST_MakePoint(lon, lat), 4326)` for points.

---

## How the data was made

1. Downloaded every road and path (`highway=*`) inside the DKI Jakarta boundary from the Overpass API,
   in 57 tiles, plus tagged points on those roads (gates, signals, crossings, speed bumps, railway crossings).
2. Removed features that are not usable roads: under construction, proposed, raceways, rest areas,
   bus stops, platforms, and areas (`area=yes`).
3. Cut each road into segments at intersections, road ends and tagged points.
4. Calculated length, one-way direction (roads tagged `oneway=-1` are flipped so they run `u` to `v`),
   and walking and cycling permission.

The scripts are in the parent folder (`/root/osm-jakarta`). See `../README.md` for how to run them again.
