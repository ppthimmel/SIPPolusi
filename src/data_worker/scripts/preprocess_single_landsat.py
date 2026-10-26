"""Convert one Landsat scene folder to a versioned, training-compatible inference Parquet."""
from pathlib import Path
import argparse
import json
import tempfile
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from _single_product_io import BASE, write_single_product

def convert_landsat(source, processed):
    import preprocess_landsat as batch
    from rasterio.transform import Affine
    from satellite_times import utc, utc_column, production_proxy

    folder = source if source.is_dir() else source.parent
    metadata_path = folder / 'metadata.json'
    item = json.loads(metadata_path.read_text(encoding='utf-8-sig'))['item']
    props = item['properties']
    config = json.loads((processed / 'landsat/manifest.json').read_text(encoding='utf-8'))
    training = sorted((processed / 'landsat/scenes').glob('*.parquet'))[0]
    base = pd.read_parquet(training, columns=['grid_id', 'easting', 'northing', 'longitude', 'latitude'])
    if base.grid_id.duplicated().any():
        raise ValueError('Duplicate training grid ID')
    indices = base.grid_id.str.extract(r'^r(\d+)_c(\d+)$').astype(int)
    rows, cols = indices[0].to_numpy(), indices[1].to_numpy()
    spec = config['grid']
    affine = Affine(*spec['transform'][:6])
    grid = dict(transform=affine, width=spec['width'], height=spec['height'],
                crs=config['config']['crs'], resolution=config['config']['resolution_m'])
    grid['active'] = np.zeros((grid['height'], grid['width']), dtype=bool)
    grid['active'][rows, cols] = True
    if grid['crs'] != 'EPSG:32748' or grid['resolution'] != 100:
        raise ValueError('Expected training Landsat grid at 100 m EPSG:32748')
    np.testing.assert_allclose(base.easting, affine.c+(cols+.5)*100, rtol=0, atol=1e-6)
    np.testing.assert_allclose(base.northing, affine.f-(rows+.5)*100, rtol=0, atol=1e-6)
    observed = utc(props['datetime'])
    produced = production_proxy(batch.production_time(folder), observed)
    if pd.isna(observed) or pd.isna(produced):
        raise ValueError('Landsat datetime and mtl.json DATE_PRODUCT_GENERATED are required')
    scene = dict(id=item['id'], folder=folder, item=item, observed=observed)
    files = [metadata_path, folder/'mtl.json', *[folder/(k+'.tif') for k in batch.REQUIRED]]
    for path in files:
        if not path.is_file():
            raise FileNotFoundError(path)
    # The batch API writes an NPZ cache: keep it temporary, not an inference dependency.
    with tempfile.TemporaryDirectory(prefix='landsat-single-') as temporary:
        arrays, quality = batch.scene_features(scene, grid, config['config'], Path(temporary)/'cache.npz')
    frame = base.copy()
    frame['scene_id'] = item['id']
    for field, value in [('observed_at', observed), ('produced_at', produced),
                         ('catalog_created_at', utc(props.get('created')))]:
        frame[field] = utc_column(value, frame.index)
    for feature in batch.FEATURES:
        frame[feature] = arrays[feature][rows, cols]
        frame[feature+'_available'] = np.isfinite(frame[feature])
        frame[feature+'_valid_fraction'] = arrays[feature+'_fraction'][rows, cols]
    return frame, item['id'], files, [training, processed/'landsat/manifest.json'], quality, 'observed_at'



def preprocess_one(source, output_root=None, processed_root=None):
    """Convert one Landsat scene folder; reuse an identical verified version."""
    return write_single_product('landsat', source, convert_landsat, __file__, output_root, processed_root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='One Landsat scene folder')
    parser.add_argument('--output-root', type=Path, default=BASE/'datasets/processed/inference')
    parser.add_argument('--processed-root', type=Path, default=BASE/'datasets/processed')
    args = parser.parse_args()
    print(preprocess_one(args.source, args.output_root, args.processed_root))


if __name__ == '__main__':
    main()
