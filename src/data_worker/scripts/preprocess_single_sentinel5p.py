"""Convert one Sentinel-5P *.all.h5 extract to a versioned, training-compatible inference Parquet."""
from pathlib import Path
import argparse
import json
import tempfile
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from _single_product_io import BASE, write_single_product

def convert_sentinel(source, processed):
    import preprocess_sentinel5p as batch
    metadata_path = source.with_name(source.name.removesuffix('.all.h5')+'.all.json')
    grid = batch.grid_spec()
    spec = json.loads((processed/'sentinel5p/manifest.json').read_text(encoding='utf-8'))['grid']
    if spec['crs'] != batch.CRS or spec['resolution_m'] != batch.RESOLUTION or any(
            spec[k] != grid[k] for k in grid):
        raise ValueError('Sentinel grid differs from the training grid')
    pixels, record = batch.read_scene(source, metadata_path, grid)
    reference = processed/'sentinel5p/observations.parquet'
    if pixels.empty:
        frame = pd.read_parquet(reference).iloc[:0].copy()
    else:
        pixels.loc[~pixels.passes_qa, 'no2_precision_mol_m2'] = np.nan
        frame = pixels.groupby(['scene_id', 'grid_id'], as_index=False).agg(
            observed_start_at=('observed_at', 'min'), observed_at=('observed_at', 'max'),
            produced_at=('produced_at', 'first'), no2_mol_m2=('no2_mol_m2', 'median'),
            no2_precision_mol_m2=('no2_precision_mol_m2', 'median'), n_valid_pixels=('passes_qa', 'sum'))
        frame['available'] = frame.n_valid_pixels > 0
    # Even zero-pixel products must have known clocks in their source metadata.
    if record['produced_at'] is None:
        raise ValueError('Sentinel source date_created is required for inference')
    return frame, record['scene_id'], [source, metadata_path], [reference, processed/'sentinel5p/manifest.json'], record, 'observed_at'



def preprocess_one(source, output_root=None, processed_root=None):
    """Convert one Sentinel-5P *.all.h5 extract; reuse an identical verified version."""
    return write_single_product('sentinel5p', source, convert_sentinel, __file__, output_root, processed_root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='One Sentinel-5P *.all.h5 extract')
    parser.add_argument('--output-root', type=Path, default=BASE/'datasets/processed/inference')
    parser.add_argument('--processed-root', type=Path, default=BASE/'datasets/processed')
    args = parser.parse_args()
    print(preprocess_one(args.source, args.output_root, args.processed_root))


if __name__ == '__main__':
    main()
