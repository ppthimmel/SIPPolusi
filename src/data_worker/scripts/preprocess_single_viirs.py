"""Convert one VIIRS *.all.h5 extract to a versioned, training-compatible inference Parquet."""
from pathlib import Path
import argparse
import json
import tempfile
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from _single_product_io import BASE, write_single_product

def convert_viirs(source, processed):
    import preprocess_viirs as batch
    frame, record, _ = batch.read_scene(source)
    reference = processed/'viirs/daily.parquet'
    native = pq.ParquetFile(reference).read_row_group(0, columns=['viirs_cell_id', 'longitude', 'latitude']).to_pandas()
    if frame.viirs_cell_id.duplicated().any():
        raise ValueError('Duplicate VIIRS source cell')
    matched = frame[['viirs_cell_id', 'longitude', 'latitude']].merge(native, on='viirs_cell_id',
        how='left', suffixes=('', '_training'), validate='one_to_one')
    if matched.longitude_training.isna().any() or not np.allclose(
            matched[['longitude', 'latitude']], matched[['longitude_training', 'latitude_training']], rtol=0, atol=1e-10):
        raise ValueError('VIIRS extract cells/coordinates differ from the training grid; extract the same area')
    metadata_path = source.with_name(source.name.removesuffix('.all.h5')+'.all.json')
    return frame, record['scene_id'], [source, metadata_path], [reference, processed/'viirs/manifest.json'], record, 'observed_end_at'



def preprocess_one(source, output_root=None, processed_root=None):
    """Convert one VIIRS *.all.h5 extract; reuse an identical verified version."""
    return write_single_product('viirs', source, convert_viirs, __file__, output_root, processed_root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='One VIIRS *.all.h5 extract')
    parser.add_argument('--output-root', type=Path, default=BASE/'datasets/processed/inference')
    parser.add_argument('--processed-root', type=Path, default=BASE/'datasets/processed')
    args = parser.parse_args()
    print(preprocess_one(args.source, args.output_root, args.processed_root))


if __name__ == '__main__':
    main()
