"""Materialize the fixed 300/20 static-image selection with independent YOLO labels."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--roboflow-zip', type=Path, required=True)
    parser.add_argument('--openalpr-dir', type=Path, required=True)
    args = parser.parse_args()
    data = ROOT / 'data'
    selection = json.loads((data / 'selection.json').read_text())
    archive_hash = hashlib.sha256(args.roboflow_zip.read_bytes()).hexdigest()
    assert archive_hash == selection['sources']['roboflow_rxg4e']['archive_sha256'], 'Wrong source archive version'
    with zipfile.ZipFile(args.roboflow_zip) as archive:
        for record in selection['images']:
            content = (archive.read(record['source_path']) if record['source'] == 'roboflow_rxg4e'
                       else (args.openalpr_dir / record['source_path']).read_bytes())
            assert hashlib.sha256(content).hexdigest() == record['sha256'], record['name']
            image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_COLOR)
            assert image is not None and image.shape[:2] == (record['height'], record['width'])
            image_path = data / 'images' / record['split'] / record['name']
            label_path = data / 'labels' / record['split'] / Path(record['name']).with_suffix('.txt')
            image_path.parent.mkdir(parents=True, exist_ok=True)
            label_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(content)
            width, height = record['width'], record['height']
            lines = []
            for x1, y1, x2, y2 in record['boxes_xyxy']:
                assert 0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height
                lines.append(f'0 {(x1+x2)/2/width:.8f} {(y1+y2)/2/height:.8f} {(x2-x1)/width:.8f} {(y2-y1)/height:.8f}')
            label_path.write_text('\n'.join(lines) + '\n')
    print('Ready: 300 train-pool images + 20 test images; one independent YOLO label file per image.')


if __name__ == '__main__':
    main()
