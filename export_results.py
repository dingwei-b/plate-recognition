"""Generate a GitHub-readable report from saved test predictions. No model dependencies."""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def cell(value):
    return html.escape(str(value)).replace('|', '&#124;').replace('\n', ' ')


def picture(path, label, width):
    url = html.escape(path, quote=True)
    return f'<a href="{url}"><img src="{url}" alt="{cell(label)}" width="{width}"></a>'


def reading(value):
    mark = ' ✓' if value['correct'] is True else (' ✗' if value['correct'] is False else '')
    return cell(value['text'] or '(empty)') + mark


def write_report(run, output):
    records, summary = run['images'], run['summary']
    if not summary.get('comparison') or not all(r['split'] == 'test' for r in records):
        raise ValueError('Expected a comparison of test images only')
    evaluation = summary['evaluation']
    lines = [
        '# US License Plate Results', '',
        f'{len(records)} test images. Expand an image below to compare detection and text recognition results. '
        'Click any image to see it at full size.', '',
        '[Project setup and usage](../README.md) · [Saved predictions](detections.json)', '',
        '## Detection', '',
        f'{evaluation["train_images"]} training images; {evaluation["epochs"]} epochs for fine-tuned and from-scratch models.', '',
        '| YOLOv9-t model | Precision | Recall |', '| --- | ---: | ---: |',
    ]
    for label, key in [('Pretrained', 'baseline'), ('Fine-tuned', 'finetuned'), ('From scratch', 'scratch')]:
        if key in evaluation:
            metric = evaluation[key]
            lines.append(f'| {label} | {metric["precision"]:.1%} | {metric["recall"]:.1%} |')
    thresholds = evaluation['thresholds']
    lines += ['', '**Precision:** how many predicted plates were correct. '
              '**Recall:** how many labeled plates were found.', '',
              f'Confidence ≥ {thresholds["precision_recall_confidence"]:.2f}; IoU ≥ {thresholds["precision_recall_iou"]:.2f}.', '']
    if summary.get('ocr_evaluation'):
        metric = summary['ocr_evaluation']
        lines += ['## Text recognition', '',
                  f'PARSeq-Tiny, fine-tuned for {metric["epochs"]} epochs on {metric["train_crops"]} labeled plate images.', '',
                  '| OCR model | Exact plate accuracy |', '| --- | ---: |']
        for key, label in [('pretrained', 'Original OCR'), ('finetuned', 'Fine-tuned OCR')]:
            result = metric[key]
            accuracy = f'{result["accuracy"]:.1%}' if result['accuracy'] is not None else 'N/A'
            lines.append(f'| {label} | {accuracy} ({result["correct"]}/{result["total"]}) |')
        lines += ['', f'This table tests OCR alone on the same {metric["labeled_test_crops"]} plate images taken from manually labeled boxes. '
                  f'{metric["unlabeled_test_crops"]} plates without confirmed text are excluded. '
                  'The whole number must match; case, spaces, and punctuation are ignored. O/0 and I/1 remain distinct.', '',
                  'The examples below use the plate areas found by YOLO. Extra background or missing characters can change the OCR result. '
                  '✓ matches the reference; ✗ differs. Unlabeled or unmatched detections have no correctness mark.', '']
    lines += ['## Test images', '']
    for index, record in enumerate(records, 1):
        lines += ['<details open>' if index == 1 else '<details>',
                  f'<summary>{index:02d} · {cell(record["name"])}</summary>', '',
                  '**Original image**', '', picture(record['original'], 'Original image', 360), '']
        if 'references' in record:
            reference = ', '.join(r['text'] or 'Not labeled' for r in record['references'])
            lines += [f'**Reference:** {cell(reference)}', '']
        predictions = record['predictions']
        lines += ['| ' + ' | '.join(cell(p['label']) for p in predictions) + ' |',
                  '| ' + ' | '.join('---' for _ in predictions) + ' |',
                  '| ' + ' | '.join(picture(p['annotated'], p['label'] + ' detections', 240) for p in predictions) + ' |',
                  '| ' + ' | '.join(f'{len(p["detections"])} detection' + ('' if len(p['detections']) == 1 else 's') for p in predictions) + ' |', '',
                  '| Detector | Plate image and confidence | Reference | Original OCR | Fine-tuned OCR |',
                  '| --- | --- | --- | --- | --- |']
        for prediction in predictions:
            label = cell(prediction['label'])
            if not prediction['detections']:
                lines.append(f'| {label} | No detections | — | — | — |')
            for number, detection in enumerate(prediction['detections'], 1):
                image = picture(detection['crop'], f'{prediction["label"]} plate {number}', 120)
                image += f'<br>#{number} · {detection["confidence"]:.3f}'
                ocr = detection.get('ocr')
                if ocr:
                    reference = ocr['reference'] or ('No matching plate' if ocr['match'] == 'unmatched' else 'Not labeled')
                    values = [label, image, cell(reference), reading(ocr['pretrained']), reading(ocr['finetuned'])]
                else:
                    values = [label, image, '—', 'Not evaluated', 'Not evaluated']
                lines.append('| ' + ' | '.join(values) + ' |')
        lines += ['', '</details>', '']
    lines += ['## Sources', '',
              '- Models: [YOLOv9-t plate detector](https://github.com/ankandrew/open-image-models) and '
              '[PARSeq-Tiny](https://github.com/baudm/parseq).',
              '- Data: [OpenALPR US benchmark](https://github.com/openalpr/benchmarks/tree/9790ed20d475be1e940a7e2a3f492a97c2ebed9b/endtoend/us) and '
              '[Roboflow License Plate Recognition](https://universe.roboflow.com/roboflow-universe-projects/license-plate-recognition-rxg4e) '
              '([Kaggle download](https://www.kaggle.com/datasets/adilshamim8/license-plate-recognition)).', '',
              'Generated from `results/detections.json` with `python export_results.py`.', '']
    path = output / 'README.md'
    path.write_text('\n'.join(lines), encoding='utf-8')
    return path


if __name__ == '__main__':
    output = ROOT / 'results'
    print(write_report(json.loads((output / 'detections.json').read_text()), output))
