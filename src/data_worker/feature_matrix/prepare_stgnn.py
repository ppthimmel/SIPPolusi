"""Prepare grid ST-GNN inputs; OSM is reserved for mapping predictions to roads.

Basis: bundle ST-GNN 2026-10-07 (FedrianzD). Tambahan TI-AI-02 (Dokumen Desain
Tabel 3.3, isu #13): jarak ke sensor darat terdekat (``SENSOR_FEATURES``) dan
penanda ketersediaan eksplisit ``<fitur>_available`` (``AVAILABILITY_FLAGS``).
"""
from pathlib import Path
import json
import hashlib
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

TEMPORAL_VALUES = ['no2_mol_m2','ntl','temperature_2m','relative_humidity_2m',
    'wind_speed_10m','wind_direction_10m','boundary_layer_height','precipitation',
    'surface_pressure','geoscf_pm25_ugm3','geoscf_no2_ugm3']
TEMPORAL_AGES = ['no2_mol_m2_age_hours','ntl_age_hours','geoscf_pm25_age_hours','geoscf_no2_age_hours']
LAND_VALUES = ['ndvi','ndbi']
LAND_AGES = ['ndvi_age_hours','ndbi_age_hours']
ROAD_FEATURES = ['road_length_m','road_segment_count','major_road_length_fraction',
    'walkable_road_length_fraction','bikeable_road_length_fraction','oneway_road_length_fraction']
TARGETS = ['target_pm25','target_no2']
SENSOR_FEATURES = ['dist_nearest_sensor_m']
# Satu penanda per kolom nilai: True tepat bila nilainya finite (tanpa imputasi).
FLAGGED_VALUES = [*TEMPORAL_VALUES,*LAND_VALUES,'lst_c',*SENSOR_FEATURES]
AVAILABILITY_FLAGS = [f'{value}_available' for value in FLAGGED_VALUES]


def stgnn_hash(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            result.update(block)
    return result.hexdigest()


def as_nullable_text(frame):
    """Teks sebagai dtype ``string`` (pd.NA), sama pada pandas 2 dan 3."""
    for column in frame.columns:
        if pd.api.types.is_string_dtype(frame[column].dtype):
            frame[column]=frame[column].astype('string')
    return frame


def stgnn_hour(value):
    value=pd.Timestamp(value)
    value=value.tz_localize('UTC') if value.tzinfo is None else value.tz_convert('UTC')
    if pd.isna(value) or value!=value.floor('h'):
        raise ValueError('Use whole UTC hours')
    return value


def satellite_events(source,feature):
    """Prepare the same valid-last-observation policy once per source batch."""
    valid=source[feature+'_available'] & np.isfinite(source[feature])
    records=source.loc[valid,['source_cell_id',feature,'observed_at','produced_at','scene_id']].copy()
    records.source_cell_id=records.source_cell_id.astype(str)
    records=records.sort_values(['source_cell_id','produced_at','observed_at','scene_id'])
    observed=records.observed_at.astype('int64')
    latest=observed.groupby(records.source_cell_id).cummax()
    records=records.loc[observed.eq(latest)].drop_duplicates(['source_cell_id','produced_at'],keep='last')
    return records.sort_values('produced_at').reset_index(drop=True)


def join_satellite_events(batch,nodes,events,feature,source_name,max_age=None):
    """Vectorised causal join; keep exactly the direct reader's source policy."""
    result=batch.copy()
    result[feature]=np.nan
    for clock in ['observed_at','produced_at']:
        result[feature+'_'+clock]=pd.Series(pd.NaT,index=result.index,dtype='datetime64[ns, UTC]')
    result[feature+'_scene_id']=pd.Series(pd.NA,index=result.index,dtype='string')
    cells=result.grid_id.map(nodes.set_index('grid_id')[source_name+'_cell_id'])
    left=result.loc[cells.notna(),['time_utc']].copy()
    left['source_cell_id']=cells.loc[left.index].astype(str)
    left['_position']=left.index
    if len(events) and len(left):
        joined=pd.merge_asof(left.sort_values('time_utc'),events,
            left_on='time_utc',right_on='produced_at',by='source_cell_id',direction='backward')
        joined=joined.set_index('_position')
        result.loc[joined.index,feature]=joined[feature]
        for clock in ['observed_at','produced_at']:
            result.loc[joined.index,feature+'_'+clock]=joined[clock]
        result.loc[joined.index,feature+'_scene_id']=joined.scene_id.astype('string')
    age=feature+'_age_hours'
    result[age]=(result.time_utc-result[feature+'_observed_at']).dt.total_seconds()/3600
    if max_age is not None:
        result.loc[result[age]>max_age,feature]=np.nan
    return result


def build_grid_graph(grid, labelled_grid_ids, context_hops=1):
    """Eight-neighbour graph, restricted to labels and their active-grid context."""
    if not isinstance(context_hops,int) or context_hops<1:
        raise ValueError('At least one context hop required')
    if not grid.grid_id.is_unique or grid.grid_id.isna().any():
        raise ValueError('Invalid grid IDs')
    positions=grid.grid_id.str.extract(r'^r(\d+)_c(\d+)$')
    if positions.isna().any().any():
        raise ValueError('Invalid grid ID format')
    pairs=list(map(tuple,positions.astype(int).to_numpy()))
    coordinate_to_id=dict(zip(pairs,grid.grid_id))
    id_to_coordinate=dict(zip(grid.grid_id,pairs))
    seeds=set(labelled_grid_ids)
    if not seeds or not seeds.issubset(id_to_coordinate):
        raise ValueError('Unknown or empty labelled grid set')
    selected={id_to_coordinate[cell] for cell in seeds}
    frontier=set(selected)
    offsets=[(dr,dc) for dr in [-1,0,1] for dc in [-1,0,1] if dr or dc]
    for _ in range(context_hops):
        neighbours={(r+dr,c+dc) for r,c in frontier for dr,dc in offsets}
        frontier=(neighbours & coordinate_to_id.keys())-selected
        selected.update(frontier)
    ids=[coordinate_to_id[position] for position in sorted(selected)]
    nodes=grid.set_index('grid_id').loc[ids,['easting','northing','longitude','latitude']].reset_index()
    nodes.insert(0,'node_index',np.arange(len(nodes),dtype='int64'))
    coordinate_to_node={id_to_coordinate[cell]:i for i,cell in enumerate(ids)}
    links=[]
    for (r,c),source in coordinate_to_node.items():
        for dr,dc in offsets:
            target=coordinate_to_node.get((r+dr,c+dc))
            if target is not None:
                distance=float(np.hypot(nodes.easting.iloc[source]-nodes.easting.iloc[target],
                                        nodes.northing.iloc[source]-nodes.northing.iloc[target]))
                if not np.isclose(distance,100*np.hypot(dr,dc),rtol=0,atol=1e-5):
                    raise ValueError('Graph centres do not match the 100 m grid')
                links.append((source,target,distance))
    edges=pd.DataFrame(links,columns=['source_node','target_node','distance_m'])
    edges=edges.astype({'source_node':'int64','target_node':'int64','distance_m':'float64'})
    return nodes,edges


def prepare_stgnn_labels(targets,nodes,train_end,validation_end):
    """Preserve station measurements; a node can have multiple station labels."""
    train_end,validation_end=stgnn_hour(train_end),stgnn_hour(validation_end)
    if train_end>=validation_end:
        raise ValueError('Invalid split boundaries')
    if targets.duplicated(['station_uuid','time_utc']).any():
        raise ValueError('Duplicate station/hour label')
    if not targets[TARGETS].notna().any(axis=1).all():
        raise ValueError('Unlabelled row in label table')
    columns=['station_uuid','station_name','station_code','station_type','grid_id','time_utc',
             'station_latitude','station_longitude','station_grid_distance_m',
             'target_window_start_utc',*TARGETS,'target_pm25_n_readings','target_no2_n_readings']
    labels=targets[columns].merge(nodes[['grid_id','node_index']],on='grid_id',
        how='left',validate='many_to_one')
    if labels.node_index.isna().any():
        raise ValueError('Ground-truth grid is outside the selected graph')
    labels['split']=np.where(labels.time_utc<train_end,'train',
                            np.where(labels.time_utc<validation_end,'validation','test'))
    if set(labels.split)!= {'train','validation','test'}:
        raise ValueError('Chronological boundaries must leave labels in all three splits')
    labels=as_nullable_text(labels)
    return labels.sort_values(['time_utc','station_uuid']).reset_index(drop=True)


def check_grid_feature_batch(batch,nodes,start,end):
    hours=int((stgnn_hour(end)-stgnn_hour(start))/pd.Timedelta(hours=1))
    if len(batch)!=len(nodes)*hours or batch.duplicated(['grid_id','time_utc']).any():
        raise ValueError('Incomplete or duplicate graph feature batch')
    if not batch.time_utc.ge(start).all() or not batch.time_utc.lt(end).all():
        raise ValueError('Feature time outside requested interval')
    if set(batch.grid_id)!=set(nodes.grid_id):
        raise ValueError('Grid feature batch has a different node set')
    if set(TARGETS)&set(batch.columns):
        raise ValueError('Labels leaked into predictors')
    for feature in [*LAND_VALUES,'lst_c','no2_mol_m2','ntl']:
        valid=batch[feature].notna()
        for clock in ['observed_at','produced_at']:
            value=batch[feature+'_'+clock]
            if value.loc[valid].isna().any() or value.loc[valid].gt(batch.loc[valid,'time_utc']).any():
                raise ValueError('Unknown or future satellite observation/production time')
    for variable in ['pm25','no2']:
        feature='geoscf_pm25_ugm3' if variable=='pm25' else 'geoscf_no2_ugm3'
        valid=batch[feature].notna()
        for clock in ['time_window_end','available_at_utc']:
            value=batch[f'geoscf_{variable}_{clock}']
            if value.loc[valid].isna().any() or value.loc[valid].gt(batch.loc[valid,'time_utc']).any():
                raise ValueError('Unknown or future GEOS condition/availability')


def sensor_distances(nodes,sensors):
    """Jarak (m, EPSG:32748) pusat setiap node ke stasiun terdekat di ``sensors``.

    ``sensors`` memuat ``station_longitude``/``station_latitude``. Pada evaluasi
    lokasi tak terlihat (TI-AI-03), berikan hanya stasiun non-uji per fold.
    """
    from .grid import nearest_sensor_distance
    sensors=sensors.drop_duplicates(['station_longitude','station_latitude'])
    return nearest_sensor_distance(nodes.easting,nodes.northing,
        sensors.station_longitude.to_numpy(),sensors.station_latitude.to_numpy())


def add_availability_flags(batch):
    for value,flag in zip(FLAGGED_VALUES,AVAILABILITY_FLAGS):
        batch[flag]=np.isfinite(batch[value].to_numpy(dtype='float64'))
    return batch


def check_availability_flags(batch):
    unexpected=set(batch.select_dtypes(include=['bool','boolean']).columns)-set(AVAILABILITY_FLAGS)
    if unexpected:
        raise ValueError(f'Unexpected Boolean column in feature table: {sorted(unexpected)}')
    for value,flag in zip(FLAGGED_VALUES,AVAILABILITY_FLAGS):
        if not batch[flag].eq(np.isfinite(batch[value].to_numpy(dtype='float64'))).all():
            raise ValueError(f'{flag} inconsistent with missing {value}')


def export_stgnn_inputs(nodes,edges,labels,reader,output_dir,
                        window=24,horizon_hours=0,chunk_hours=24,provenance=None,sensors=None):
    """Stream feature histories from the direct individual-dataset reader.

    ``sensors``: stasiun untuk ``dist_nearest_sensor_m``; bawaan seluruh stasiun
    berlabel (evaluasi transduktif).
    """
    if window<1 or horizon_hours<0 or chunk_hours<1:
        raise ValueError('Invalid temporal settings')
    sensors=labels if sensors is None else sensors
    start=labels.time_utc.min()-pd.Timedelta(hours=window-1+horizon_hours)
    end=labels.time_utc.max()+pd.Timedelta(hours=1)
    output_dir=Path(output_dir)
    output_dir.mkdir(parents=True,exist_ok=True)
    manifest=dict(status='running', task='grid pollution estimation; roads receive grid predictions later',
        nodes=len(nodes),edges=len(edges),labels=len(labels),
        start_utc=start.isoformat(),end_utc_exclusive=end.isoformat(),
        window=window,horizon_hours=horizon_hours,chunk_hours=chunk_hours,
        temporal_values=TEMPORAL_VALUES,temporal_ages=TEMPORAL_AGES,
        land_values=LAND_VALUES,land_ages=LAND_AGES,road_features=[],
        sensor_features=SENSOR_FEATURES,availability_flags=AVAILABILITY_FLAGS,
        splits=labels.split.value_counts().to_dict(),
        label_policy='Station-hour labels only; no spatial target interpolation. Multiple stations in a grid retain distinct label rows.',
        loss_policy='Gather node predictions using label node_index/time. Compute loss only on finite target values. No stored Boolean mask.',
        graph_policy='Eight-neighbour bidirectional adjacency, no self edges; configure model self-loops explicitly. Transductive temporal split, not unseen-location evaluation.',
        feature_policy='Direct source reader, all context-grid hours including unlabelled hours. Missing feature values remain NaN. No preprocessing statistics fitted here.',
        static_policy='Landsat only: slow-changing land context selected causally at each hour, not a TCN input.',
        osm_policy='Excluded from predictors and graph construction. Road mapping is used only after grid pollution prediction.',
        metadata_policy='Source clocks/IDs are provenance, not model predictors. GEOS NO2 ppb duplicate omitted.',
        sensor_policy='dist_nearest_sensor_m: Euclidean EPSG:32748 distance from the cell centre to the nearest '
            'station in the supplied set (default: all labelled stations, transductive). Recompute per fold '
            'without held-out stations for unseen-location evaluation.',
        availability_policy='One Boolean <value>_available per value column, True exactly when the value is '
            'finite. Missing values remain NaN; no imputation. Flags are not model predictors by default.',
        sensor_stations=int(sensors[['station_longitude','station_latitude']].drop_duplicates().shape[0]),
        normalisation_policy='Train-only fit in model training; never fit on validation/test. Labels are not predictors.',
        provenance=provenance or {})
    manifest_path=output_dir/'manifest.json'
    manifest_path.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    for name,table in [('nodes.parquet',nodes),('edges.parquet',edges),('labels.parquet',labels)]:
        table.to_parquet(output_dir/name,index=False,compression='zstd')
        pd.testing.assert_frame_equal(table,pd.read_parquet(output_dir/name))
    static=nodes[['node_index','grid_id']].assign(dist_nearest_sensor_m=sensor_distances(nodes,sensors))
    writer=None
    schema=None
    rows=0
    example=None
    try:
        for first in pd.date_range(start,end,freq=pd.Timedelta(hours=chunk_hours),inclusive='left'):
            last=min(first+pd.Timedelta(hours=chunk_hours),end)
            batch=reader(nodes.grid_id.tolist(),first,last)
            check_grid_feature_batch(batch,nodes,first,last)
            batch=batch.drop(columns=['geoscf_no2_ppb',*ROAD_FEATURES],errors='ignore').merge(static,
                on='grid_id',how='left',validate='many_to_one')
            batch=add_availability_flags(batch)
            check_availability_flags(batch)
            batch=as_nullable_text(batch)
            for column in [*TEMPORAL_VALUES,*TEMPORAL_AGES,*LAND_VALUES,*LAND_AGES,'lst_c','lst_c_age_hours',
                           *SENSOR_FEATURES]:
                batch[column]=batch[column].astype('float32')
            batch=batch.sort_values(['time_utc','node_index']).reset_index(drop=True)
            table=pa.Table.from_pandas(batch,preserve_index=False)
            # Preserve nullable text dtypes without carrying arbitrary frame attrs.
            table=table.replace_schema_metadata({b'pandas':table.schema.metadata[b'pandas']})
            if writer is None:
                schema=table.schema
                writer=pq.ParquetWriter(output_dir/'features.parquet',schema,compression='zstd')
                example=batch.head(3).copy()
            if table.schema!=schema:
                raise ValueError('Feature schema changed across batches')
            writer.write_table(table)
            rows+=len(batch)
            print(f'ST-GNN features: {rows:,} rows through {last.isoformat()}',flush=True)
    finally:
        if writer is not None:
            writer.close()
    expected=len(nodes)*int((end-start)/pd.Timedelta(hours=1))
    if rows!=expected or pq.ParquetFile(output_dir/'features.parquet').metadata.num_rows!=expected:
        raise ValueError('Feature export row count differs')
    # Chunked read-back and label lookups keep memory bounded.
    label_keys=pd.MultiIndex.from_frame(labels[['grid_id','time_utc']])
    found=set()
    read_rows=0
    for row_group in range(pq.ParquetFile(output_dir/'features.parquet').num_row_groups):
        parquet=pq.ParquetFile(output_dir/'features.parquet')
        restored=parquet.read_row_group(row_group).to_pandas()
        read_rows+=len(restored)
        if row_group==0:
            pd.testing.assert_frame_equal(example,restored.head(3).reset_index(drop=True))
        keys=pd.MultiIndex.from_frame(restored[['grid_id','time_utc']])
        if keys.has_duplicates:
            raise ValueError('Duplicate exported feature key')
        found.update(keys.intersection(label_keys).tolist())
    if read_rows!=expected or not set(label_keys).issubset(found):
        raise ValueError('Labels do not match exported feature grid/hours')
    manifest.update(status='complete',feature_rows=rows,feature_columns=schema.names,
                    label_grids=int(labels.grid_id.nunique()),context_grids=int(len(nodes)-labels.grid_id.nunique()),
                    outputs={name:stgnn_hash(output_dir/name) for name in
                             ['features.parquet','nodes.parquet','edges.parquet','labels.parquet']})
    manifest_path.write_text(json.dumps(manifest,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    return manifest


def read_stgnn_batch(directory,target_time,target='target_pm25',sensors=None):
    """Return raw arrays for one complete graph/window; transforms belong to training.

    ``sensor_static`` berisi ``dist_nearest_sensor_m`` yang diekspor, atau dihitung
    ulang dari ``sensors`` (mis. stasiun non-uji satu fold).
    """
    directory=Path(directory)
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    if manifest['status']!='complete' or target not in TARGETS:
        raise ValueError('Require completed inputs and a supported target')
    target_time=stgnn_hour(target_time)
    cutoff=target_time-pd.Timedelta(hours=manifest['horizon_hours'])
    times=pd.date_range(cutoff-pd.Timedelta(hours=manifest['window']-1),cutoff,freq='h')
    nodes=pd.read_parquet(directory/'nodes.parquet').sort_values('node_index')
    edges=pd.read_parquet(directory/'edges.parquet')
    columns=['node_index','grid_id','time_utc',*TEMPORAL_VALUES,*TEMPORAL_AGES,*LAND_VALUES,*LAND_AGES,
             *SENSOR_FEATURES]
    history=pd.read_parquet(directory/'features.parquet',columns=columns,
        filters=[('time_utc','>=',times[0]),('time_utc','<=',times[-1])])
    if len(history)!=len(nodes)*len(times) or history.duplicated(['node_index','time_utc']).any():
        raise ValueError('Requested window is outside complete exported feature history')
    index=pd.MultiIndex.from_product([nodes.node_index,times],names=['node_index','time_utc'])
    history=history.set_index(['node_index','time_utc']).reindex(index)
    if history.grid_id.isna().any():
        raise ValueError('Missing grid/hour feature row')
    temporal=history[[*TEMPORAL_VALUES,*TEMPORAL_AGES]].to_numpy(dtype='float32').reshape(
        len(nodes),len(times),len(TEMPORAL_VALUES)+len(TEMPORAL_AGES))
    last=history.xs(cutoff,level='time_utc').reindex(nodes.node_index)
    land_static=last[[*LAND_VALUES,*LAND_AGES]].to_numpy(dtype='float32')
    if sensors is None:
        sensor_static=last[SENSOR_FEATURES].to_numpy(dtype='float32')
    else:
        sensor_static=sensor_distances(nodes,sensors).astype('float32')[:,None]
    labels=pd.read_parquet(directory/'labels.parquet',filters=[('time_utc','==',target_time)])
    return dict(temporal=temporal,land_static=land_static,sensor_static=sensor_static,
        edge_index=edges[['source_node','target_node']].to_numpy(dtype='int64').T,
        label_node_index=labels.node_index.to_numpy(dtype='int64'),
        label_values=labels[target].to_numpy(dtype='float32'),
        label_stations=labels.station_uuid.to_numpy(),grid_ids=nodes.grid_id.to_numpy(),
        target_time=target_time,input_cutoff=cutoff,
        temporal_columns=[*TEMPORAL_VALUES,*TEMPORAL_AGES],
        land_static_columns=[*LAND_VALUES,*LAND_AGES],sensor_static_columns=SENSOR_FEATURES)


def grid_predictions_to_roads(predictions,mapping,value_column='prediction'):
    """Length-weighted road estimates; report missing predicted-grid coverage."""
    if predictions.duplicated(['grid_id','time_utc']).any():
        raise ValueError('Duplicate grid/hour prediction')
    valid=predictions.loc[np.isfinite(predictions[value_column]),['grid_id','time_utc',value_column]]
    joined=mapping.merge(valid,on='grid_id',how='inner',validate='many_to_many')
    joined['_weighted']=joined[value_column]*joined.weight
    result=joined.groupby(['graph_version','edge_id','time_utc'],as_index=False).agg(
        _weighted=('_weighted','sum'),predicted_grid_coverage=('weight','sum'))
    result['prediction']=result._weighted/result.predicted_grid_coverage
    return result.drop(columns='_weighted')
