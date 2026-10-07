"""Evaluate both OCR weights once on test crops and enrich every YOLO test detection."""
import json
from urllib.parse import unquote
from PIL import Image
from ocr_runtime import ROOT, Recognizer, sha256


def iou(a, b):
    overlap = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - overlap
    return overlap / union if union else 0.0


def match_detections(detections, truth):
    candidates = sorted(((iou(d['xyxy'], t['xyxy']), di, ti)
                         for di, d in enumerate(detections) for ti, t in enumerate(truth)), reverse=True)
    matches, used = {}, set()
    for score, di, ti in candidates:
        if score >= 0.5 and di not in matches and ti not in used:
            matches[di] = ti
            used.add(ti)
    return matches


def enrich(run):
    manifest = ROOT / 'data/ocr/test.json'
    truth = json.loads(manifest.read_text())
    report = json.loads((ROOT / 'runs/ocr-finetune/training.json').read_text())
    assert report['train_manifest_sha256'] == sha256(ROOT / 'data/ocr/train.json'), 'Training labels changed; retrain OCR'
    assert all(r['split'] == 'test' for r in truth)
    assert {r['name'] for r in run['images']} == {r['name'] for r in truth}
    assert all(r['split'] == 'test' for r in run['images'])
    inputs = json.loads((ROOT / 'runs/ocr-finetune/training_inputs.json').read_text())
    assert all(r['split'] == 'train' for r in inputs)
    assert {r['image_sha256'] for r in inputs}.isdisjoint({r['image_sha256'] for r in truth})
    by_name = {}
    for row in truth:
        assert sha256(ROOT / row['crop']) == row['crop_sha256'], row['crop']
        assert sha256(ROOT / 'data/images/test' / row['name']) == row['image_sha256'], row['name']
        by_name.setdefault(row['name'], []).append(row)
    labeled = [r for r in truth if r['text']]
    gt_images = [Image.open(ROOT / r['crop']).convert('RGB') for r in labeled]
    items, crops = [], []
    for record in run['images']:
        references = by_name[record['name']]
        record['references'] = [{'xyxy': t['xyxy'], 'text': t['text']} for t in references]
        for detector in record['predictions']:
            matches = match_detections(detector['detections'], references)
            for index, detection in enumerate(detector['detections']):
                reference = references[matches[index]]['text'] if index in matches else None
                status = 'labeled' if reference else ('unlabeled' if index in matches else 'unmatched')
                detection['ocr'] = dict(reference=reference, match=status)
                items.append(detection)
                crops.append(Image.open(ROOT / 'results' / unquote(detection['crop'])).convert('RGB'))
    evaluation = dict(model='PARSeq-Tiny', train_crops=report['train_crops'], epochs=report['completed_epochs'],
                      test_images=len(run['images']), labeled_test_crops=len(labeled),
                      unlabeled_test_crops=len(truth) - len(labeled),
                      metric='Exact normalized plate match on ground-truth crops; unlabeled crops excluded.',
                      normalization='Uppercase A-Z/0-9; ignore spaces/punctuation; no O/0 or I/1 substitution.',
                      test_manifest_sha256=sha256(manifest), train_manifest_sha256=report['train_manifest_sha256'],
                      image_hash_overlap=0, checkpoint_selection=report['checkpoint_selection'])
    gt_records = [{k: r[k] for k in ('name', 'plate_index', 'text', 'crop')} for r in labeled]
    for key in ('pretrained', 'finetuned'):
        path = ROOT / 'models' / f'parseq-tiny-{key}.pt'
        expected = report['initial_weights_sha256' if key == 'pretrained' else 'final_weights_sha256']
        assert sha256(path) == expected, 'OCR weights changed; retrain/re-evaluate consistently'
        recognizer = Recognizer(path)
        predictions = recognizer.predict(gt_images + crops)
        gt_predictions, detected_predictions = predictions[:len(labeled)], predictions[len(labeled):]
        correct = sum(p['text'] == t['text'] for p, t in zip(gt_predictions, labeled))
        evaluation[key] = dict(correct=correct, total=len(labeled), accuracy=correct / len(labeled) if labeled else None,
                               weights_sha256=sha256(path))
        for record, prediction in zip(gt_records, gt_predictions):
            record[key] = dict(prediction, correct=prediction['text'] == record['text'])
        for detection, prediction in zip(items, detected_predictions):
            reference = detection['ocr']['reference']
            detection['ocr'][key] = dict(prediction, correct=prediction['text'] == reference if reference else None)
        print(f'{key}: {correct}/{len(labeled)} exact matches on GT crops; {len(crops)} detector crops processed', flush=True)
        del recognizer
    evaluation['detector_crops_processed'] = len(crops)
    # End-to-end counts include misses; useful for auditing, separate from isolated OCR accuracy.
    evaluation['end_to_end'] = {}
    for detector_key in ('baseline', 'finetuned', 'scratch'):
        per_detector = {}
        for ocr_key in ('pretrained', 'finetuned'):
            correct = sum(d['ocr'][ocr_key]['correct'] is True for r in run['images']
                          for p in r['predictions'] if p['key'] == detector_key for d in p['detections'])
            per_detector[ocr_key] = dict(correct=correct, labeled_plates=len(labeled), accuracy=correct / len(labeled))
        evaluation['end_to_end'][detector_key] = per_detector
    run['summary']['ocr_evaluation'] = evaluation
    (ROOT / 'runs/ocr-comparison.json').write_text(json.dumps(dict(summary=evaluation, ground_truth_crops=gt_records), indent=2) + '\n')
    return run


def main():
    from detect import gallery
    path = ROOT / 'results/detections.json'
    run = enrich(json.loads(path.read_text()))
    path.write_text(json.dumps(run, indent=2) + '\n')
    gallery(run['images'], path.parent, run['summary'])
    print(path.parent / 'index.html')


if __name__ == '__main__':
    main()
