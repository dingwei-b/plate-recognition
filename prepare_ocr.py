"""Build small JSON manifests and GT crops, preserving the existing train/test split."""
import json
from pathlib import Path
from PIL import Image
from ocr_runtime import ROOT, normalize, sha256


def main():
    selection = json.loads((ROOT / 'data/selection.json').read_text())
    manual = {r['name']: r for r in json.loads((ROOT / 'data/ocr/manual_labels.json').read_text())}
    records = {'train': [], 'test': []}
    for image in selection['images']:
        split, name = image['split'], image['name']
        path = ROOT / 'data/images' / split / name
        assert sha256(path) == image['sha256'], name
        if image['source'] == 'openalpr_us':
            label_path = ROOT / 'data/source/openalpr_text' / Path(image['source_path']).with_suffix('.txt').name
            lines = [line.split() for line in label_path.read_text().splitlines() if line.strip()]
            labels = []
            for fields in lines:
                x, y, w, h = map(int, fields[1:5])
                labels.append(([x, y, x + w, y + h], normalize(''.join(fields[5:]))))
            provenance = 'OpenALPR upstream text annotation (pinned revision in data/selection.json)'
        else:
            labels = [(box, manual[name]['text'] if i == 0 else None)
                      for i, box in enumerate(image['boxes_xyxy'])]
            provenance = manual[name]['status']
        for index, box in enumerate(image['boxes_xyxy']):
            matched = [text for coordinates, text in labels if coordinates == box]
            assert len(matched) == 1, (name, box, labels)
            text = matched[0]
            crop_path = ROOT / 'data/ocr/crops' / split / f'{Path(name).stem}_{index + 1}.png'
            crop_path.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(path) as im:
                im.convert('RGB').crop(box).save(crop_path)
            records[split].append(dict(name=name, plate_index=index, split=split, xyxy=box, text=text,
                                       crop=str(crop_path.relative_to(ROOT)), image_sha256=image['sha256'],
                                       crop_sha256=sha256(crop_path), provenance=provenance))
    train_hashes = {r['image_sha256'] for r in records['train']}
    test_hashes = {r['image_sha256'] for r in records['test']}
    assert train_hashes.isdisjoint(test_hashes), 'Cross-split image duplicate'
    assert {r['crop_sha256'] for r in records['train']}.isdisjoint({r['crop_sha256'] for r in records['test']})
    for split, rows in records.items():
        (ROOT / 'data/ocr' / f'{split}.json').write_text(json.dumps(rows, indent=2) + '\n')
    audit = dict(selection_sha256=sha256(ROOT / 'data/selection.json'), image_hash_overlap=0, crop_hash_overlap=0,
                 normalization='Uppercase A-Z and 0-9; remove spaces/punctuation; no O/0 or I/1 substitutions.',
                 counts={s: dict(total=len(r), labeled=sum(bool(x['text']) for x in r),
                                 excluded=sum(not x['text'] for x in r)) for s, r in records.items()})
    (ROOT / 'data/ocr/audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    print(json.dumps(audit, indent=2))


if __name__ == '__main__':
    main()
