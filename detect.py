"""Detect plates, save crops and annotated images, and build a local comparison page."""
import argparse
import html
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import quote

import cv2
import numpy as np
import onnxruntime as ort

ROOT = Path(__file__).resolve().parent


def prepare(image, size):
    height, width = image.shape[:2]
    ratio = min(size / width, size / height)
    resized_w, resized_h = round(width * ratio), round(height * ratio)
    dx, dy = (size - resized_w) / 2, (size - resized_h) / 2
    resized = cv2.resize(image, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
    padded = cv2.copyMakeBorder(resized, round(dy - 0.1), round(dy + 0.1),
                               round(dx - 0.1), round(dx + 0.1), cv2.BORDER_CONSTANT, value=(114, 114, 114))
    tensor = np.ascontiguousarray(padded[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255
    return tensor, ratio, dx, dy


def predict(session, image, threshold):
    if hasattr(session, "predict"):
        return session.predict(image, threshold)
    height, width = image.shape[:2]
    model_input = session.get_inputs()[0]
    tensor, ratio, dx, dy = prepare(image, model_input.shape[-1])
    raw = np.asarray(session.run(None, {model_input.name: tensor})[0])
    if raw.ndim == 3 and raw.shape[0] == 1:
        raw = raw[0]
    if raw.ndim != 2 or raw.shape[1] != 7:
        raise ValueError(f"Expected YOLO end-to-end output [N, 7], got {raw.shape}")
    results = []
    # Output: batch_index, x1, y1, x2, y2, class_id, confidence. NMS is in the ONNX graph.
    for row in raw:
        if not np.isfinite(row).all() or row[6] < threshold or int(row[5]) != 0:
            continue
        x1 = max(0, min(width, math.floor((float(row[1]) - dx) / ratio)))
        y1 = max(0, min(height, math.floor((float(row[2]) - dy) / ratio)))
        x2 = max(0, min(width, math.ceil((float(row[3]) - dx) / ratio)))
        y2 = max(0, min(height, math.ceil((float(row[4]) - dy) / ratio)))
        if x2 > x1 and y2 > y1:
            results.append({"xyxy": [x1, y1, x2, y2], "confidence": round(float(row[6]), 5)})
    return sorted(results, key=lambda r: r["confidence"], reverse=True)


def save_image(path, image):
    if not cv2.imwrite(str(path), image):
        raise IOError(f"Cannot write image: {path}")


def relative(path, output):
    return quote(os.path.relpath(path, output).replace(os.sep, "/"), safe="/")


def crop_card(detection, index, label='Plate'):
    crop = detection['crop']
    content = (f'<div class="crop-card"><a href="{crop}" target="_blank"><img loading="lazy" src="{crop}" '
               f'alt="{html.escape(label)} detected plate"><span>#{index} · {detection["confidence"]:.3f}</span></a>')
    if 'ocr' in detection:
        ocr = detection['ocr']
        reference = ocr['reference'] or ('No matching plate' if ocr['match'] == 'unmatched' else 'Not labeled')
        content += f'<div class="ocr"><p><span>Reference</span><code>{html.escape(reference)}</code></p>'
        for key, name in [('pretrained', 'Original OCR'), ('finetuned', 'Fine-tuned OCR')]:
            value = ocr[key]
            status = 'correct' if value['correct'] is True else ('incorrect' if value['correct'] is False else 'unchecked')
            mark = ' ✓' if value['correct'] is True else (' ✗' if value['correct'] is False else '')
            content += (f'<p class="{status}"><span>{name}</span><code>'
                        f'{html.escape(value["text"] or "(empty)")}{mark}</code></p>')
        content += '</div>'
    return content + '</div>'


def gallery(records, output, summary):
    """Self-contained gallery: pagination works over file:// as well as HTTP."""
    cards = []
    splits = sorted({r.get('split', 'sample') for r in records})
    for record in records:
        if "predictions" in record:
            figures = [f'<figure><figcaption>Original image</figcaption><a href="{record["original"]}" target="_blank"><img loading="lazy" src="{record["original"]}" alt="Original image"></a></figure>']
            if 'references' in record:
                text = ', '.join(r['text'] or 'Not labeled' for r in record['references'])
                figures[0] = figures[0].replace('</figure>', f'<p class="reference">Reference: <code>{html.escape(text)}</code></p></figure>')
            for prediction in record["predictions"]:
                label = html.escape(prediction["label"])
                crops = ''.join(crop_card(d, i, prediction['label']) for i, d in enumerate(prediction["detections"], 1))
                figures.append(f'<figure data-model="{prediction["key"]}"><figcaption>{label}</figcaption><a href="{prediction["annotated"]}" target="_blank"><img loading="lazy" src="{prediction["annotated"]}" alt="{label} detections"></a><p class="model-stats">{len(prediction["detections"])} detections · {prediction["elapsed_ms"]:.1f} ms</p><div class="crops">{crops or "<span>No detections at this confidence threshold.</span>"}</div></figure>')
            cards.append(f'<template data-split="test"><article><h2>{html.escape(record["name"])}</h2><p>Test image · Same confidence threshold for all models</p><div class="pair comparison-grid">{"".join(figures)}</div></article></template>')
            continue
        original, annotated = record["original"], record["annotated"]
        crops = ''.join(crop_card(d, i) for i, d in enumerate(record['detections'], 1))
        split = html.escape(record.get('split', 'sample'))
        cards.append(f'''<template data-split="{split}"><article><h2>{html.escape(record['name'])}</h2>
          <p>{split.title()} · {len(record['detections'])} detections · {record['elapsed_ms']:.1f} ms</p>
          <div class="pair"><figure><figcaption>Original image</figcaption><a href="{original}" target="_blank"><img loading="lazy" src="{original}" alt="Original image"></a></figure>
          <figure><figcaption>YOLO detections</figcaption><a href="{annotated}" target="_blank"><img loading="lazy" src="{annotated}" alt="Image with predicted bounding boxes"></a></figure></div>
          <div class="crops">{crops or '<p>No plates detected. Inspect the original image for missed plates.</p>'}</div></article></template>''')
    page = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>US License Plates | Detection and Recognition</title><style>
    *{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#172536;font:15px system-ui,sans-serif}
    main{max-width:1000px;margin:auto;padding:24px}h1{font-size:26px;margin:0 0 12px}header p{line-height:1.7;color:#526273}
    article{background:white;border:1px solid #dfe6ed;border-radius:12px;padding:14px;margin:16px 0}h2{font-size:15px;overflow-wrap:anywhere;margin:0}
    article>p{color:#657588;font-size:12px;margin:8px 0}.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}figure{margin:0;min-width:0}
    figcaption{margin-bottom:6px;font-size:12px;font-weight:600}.pair img{width:100%;height:200px;object-fit:contain;background:#edf1f5;border-radius:6px}
    .crops{display:flex;gap:12px;flex-wrap:wrap;margin-top:10px}.crops a{display:flex;flex-direction:column;gap:4px;color:#526273;text-decoration:none}
    .crops img{height:60px;max-width:100%;object-fit:contain;align-self:flex-start;border:1px solid #dfe6ed}.crops span{font-size:11px}
    .controls,nav{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:16px 0}button,select{font:inherit;border:1px solid #b8c7d6;border-radius:6px;padding:8px 12px;background:white;color:#172536}
    button{cursor:pointer}button:disabled{opacity:.4;cursor:default}button:focus-visible,select:focus-visible{outline:3px solid #2181df}label{display:flex;align-items:center;gap:8px}
    #status{color:#526273}.pager{font-variant-numeric:tabular-nums}
    .evaluation{overflow-x:auto}.evaluation table{border-collapse:collapse;font-size:13px;width:100%}.evaluation th,.evaluation td{text-align:left;padding:8px;border-bottom:1px solid #dfe6ed}
    main.comparison{max-width:1440px}.comparison-grid{grid-template-columns:repeat(4,minmax(0,1fr))}.model-stats{font-size:12px;color:#657588;margin:6px 0}.comparison-grid .crops img{height:50px}.comparison-grid .crops span{font-size:11px}
    .crop-card{width:100%;min-width:0;border-top:1px solid #e5ebf0;padding-top:8px}.crop-card>a{align-items:flex-start}.crop-card img{width:auto;max-width:100%}
    .ocr{margin-top:8px;background:#f6f8fa;border-radius:6px;padding:7px}.ocr p{display:flex;justify-content:space-between;gap:6px;margin:4px 0;align-items:baseline}
    .ocr code,.reference code{font-size:13px;overflow-wrap:anywhere}.ocr code{text-align:right}.ocr .correct code{color:#14733b}.ocr .incorrect code{color:#ac3838}.reference{font-size:12px;color:#526273}
    @media(max-width:900px){.comparison-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
    @media(max-width:700px){main{padding:12px}.pair{gap:8px}.pair img{height:140px}article{padding:10px}.crops img,.comparison-grid .crops img{height:45px}}
    </style></head><body><main><header><h1>US License Plates</h1><p>Find license plates and read their numbers.</p>
    <p>Model sources: <a href="https://github.com/ankandrew/open-image-models" target="_blank" rel="noopener noreferrer">YOLOv9-t plate detector</a>
    · <a href="https://github.com/baudm/parseq" target="_blank" rel="noopener noreferrer">PARSeq-Tiny text recognizer</a></p>
    <p>Data sources: <a href="https://github.com/openalpr/benchmarks/tree/9790ed20d475be1e940a7e2a3f492a97c2ebed9b/endtoend/us" target="_blank" rel="noopener noreferrer">OpenALPR US benchmark</a>
    · <a href="https://universe.roboflow.com/roboflow-universe-projects/license-plate-recognition-rxg4e" target="_blank" rel="noopener noreferrer">Roboflow License Plate Recognition</a>
    (<a href="https://www.kaggle.com/datasets/adilshamim8/license-plate-recognition" target="_blank" rel="noopener noreferrer">Kaggle download</a>).</p>'''
    if summary.get("comparison"):
        page = page.replace('<main>', '<main class="comparison">')
        page += (f'<p>{summary["image_count"]} test images · 3 models · Confidence ≥ {summary["threshold"]}<br>'
                 'Pretrained · Fine-tuned (20 epochs) · From scratch (20 epochs). '
                 'Click any image to view full size.</p></header>')
    else:
        page += (f'<p>{summary["image_count"]} images · {summary["images_with_detections"]} with detections · '
                 f'{summary["plate_count"]} plate candidates<br>{html.escape(summary["model"])} · CPU · Confidence threshold {summary["threshold"]}. '
                 'Click any image to view full size. Each detected plate is shown separately below. '
                 'Prediction counts and confidence scores are not accuracy measurements.</p></header>')
    if summary.get("evaluation"):
        evaluation = summary["evaluation"]
        page += (f'<section class="evaluation"><p>{evaluation["train_images"]} training images · '
                 f'{evaluation["epochs"]} epochs · {evaluation["test_images"]} test images.</p>'
                 '<table><thead><tr><th>Model</th><th>Precision</th><th>Recall</th></tr></thead><tbody>')
        for label, key in [("Pretrained", "baseline"), ("Fine-tuned", "finetuned"), ("From scratch", "scratch")]:
            if key not in evaluation:
                continue
            metric = evaluation[key]
            page += '<tr><td>' + label + '</td>' + ''.join(f'<td>{metric[k]:.1%}</td>' for k in ["precision", "recall"]) + '</tr>'
        page += '</tbody></table><p><strong>Precision:</strong> Of the predicted plates, how many were correct?<br><strong>Recall:</strong> Of the labeled plates, how many were found?</p><p>Confidence ≥ 0.25, IoU ≥ 0.50.</p></section>'
    if summary.get('ocr_evaluation'):
        metric = summary['ocr_evaluation']
        page += (f'<section class="evaluation"><p><strong>PARSeq-Tiny OCR</strong> · {metric["train_crops"]} labeled plate images for training · '
                 f'{metric["epochs"]} epochs</p><table><thead><tr><th>OCR model</th><th>Exact plate accuracy</th></tr></thead><tbody>')
        for key, label in [('pretrained', 'Original OCR'), ('finetuned', 'Fine-tuned OCR')]:
            item = metric[key]
            accuracy = f'{item["accuracy"]:.1%}' if item['accuracy'] is not None else 'N/A'
            page += f'<tr><td>{label}</td><td>{accuracy} ({item["correct"]}/{item["total"]})</td></tr>'
        page += (f'</tbody></table><p>This table tests text recognition alone: both OCR models read the same '
                 f'{metric["labeled_test_crops"]} plate images taken from manually labeled boxes. '
                 f'{metric["unlabeled_test_crops"]} plates without confirmed text are excluded. '
                 'The whole plate number must match; case, spaces and punctuation are ignored.</p>'
                 '<p>Below, both OCR models read the plate areas found by YOLO. A predicted box may include extra background '
                 'or cut off characters, so these results can differ from the table. '
                 '✓ matches the reference; ✗ differs. Missed plates have no OCR result.</p></section>')
    options = ''.join(f'<option value="{html.escape(s)}">{html.escape(s.title())} ({sum(r.get("split", "sample") == s for r in records)})</option>' for s in splits)
    page += f'''<div class="controls"><label>Split <select id="split"><option value="all">All images</option>{options}</select></label>
    <label>Images per page <select id="size"><option>5</option><option selected>10</option><option>20</option><option>50</option></select></label></div>
    <p id="status" role="status" aria-live="polite"></p>'''
    nav = '''<nav aria-label="Pagination"><button type="button" class="previous">Previous</button><span class="pager"></span><button type="button" class="next">Next</button><label>Page <select class="page-number" aria-label="Go to page"></select></label></nav>'''
    page += nav + '<section id="gallery" aria-label="Image comparisons"></section>' + nav + ''.join(cards)
    page += '''<noscript>Please enable JavaScript to browse this paginated gallery.</noscript></main>
    <script>
    const templates = [...document.querySelectorAll('template[data-split]')];
    const split = document.getElementById('split'), size = document.getElementById('size');
    let page = 1;
    function render(scroll = false) {
      const selected = templates.filter(t => split.value === 'all' || t.dataset.split === split.value);
      const perPage = Number(size.value), pages = Math.max(1, Math.ceil(selected.length / perPage));
      page = Math.max(1, Math.min(page, pages));
      const start = (page - 1) * perPage;
      document.getElementById('gallery').replaceChildren(...selected.slice(start, start + perPage).map(t => t.content.cloneNode(true)));
      document.getElementById('status').textContent = selected.length ? `Showing ${start + 1}–${Math.min(start + perPage, selected.length)} of ${selected.length} images` : 'No images in this split.';
      document.querySelectorAll('.pager').forEach(el => el.textContent = `Page ${page} of ${pages}`);
      document.querySelectorAll('.previous').forEach(el => el.disabled = page === 1);
      document.querySelectorAll('.next').forEach(el => el.disabled = page === pages);
      document.querySelectorAll('.page-number').forEach(el => {
        el.replaceChildren(...Array.from({length: pages}, (_, i) => new Option(String(i + 1), String(i + 1))));
        el.value = String(page);
      });
      if (scroll) window.scrollTo({top: 0, behavior: 'instant'});
    }
    document.querySelectorAll('.previous').forEach(el => el.addEventListener('click', () => {page--; render(true);}));
    document.querySelectorAll('.next').forEach(el => el.addEventListener('click', () => {page++; render(true);}));
    document.querySelectorAll('.page-number').forEach(el => el.addEventListener('change', () => {page = Number(el.value); render(true);}));
    [split, size].forEach(el => el.addEventListener('change', () => {page = 1; render();}));
    render();
    </script></body></html>'''
    (output / "index.html").write_text(page, encoding="utf-8")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data/images/test")
    parser.add_argument("--output", type=Path, default=ROOT / "results")
    parser.add_argument("--model", type=Path, default=ROOT / "models/yolo-v9-t-384-finetuned.pt")
    parser.add_argument("--evaluation", type=Path, help="Optional metrics JSON from evaluate.py")
    parser.add_argument("--conf", type=float, default=0.25)
    args = parser.parse_args()
    if not 0 <= args.conf <= 1:
        parser.error("--conf must be between 0 and 1")
    inputs = [args.input] if args.input.is_file() else sorted(p for p in args.input.rglob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    if not inputs:
        parser.error("No input images; run prepare.py first.")
    if not args.model.is_file():
        parser.error("Model missing; run prepare.py first.")
    output = args.output.resolve()
    if any(p.resolve().parent == output / "annotated" or output / "crops" in p.resolve().parents for p in inputs):
        parser.error("Input must not be an output directory.")
    for directory in (output / "annotated", output / "crops"):
        directory.mkdir(parents=True, exist_ok=True)
    if args.model.suffix == ".pt":
        from yolo_runtime import TorchDetector
        session = TorchDetector(args.model)
    else:
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        session = ort.InferenceSession(str(args.model), sess_options=options, providers=["CPUExecutionProvider"])
    # Warm up once; reported time covers preprocessing + inference + coordinate conversion, not file IO.
    predict(session, np.zeros((384, 384, 3), dtype=np.uint8), args.conf)
    records = []
    for path in inputs:
        split = path.parent.name if path.parent.name in {"train", "test", "val"} else "sample"
        output_name = f"{split}__{path.name}"
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"Cannot read image: {path}")
        start = time.perf_counter()
        detections = predict(session, image, args.conf)
        elapsed = (time.perf_counter() - start) * 1000
        annotated = image.copy()
        thickness = max(2, round(max(image.shape[:2]) / 600))
        for i, detection in enumerate(detections, 1):
            x1, y1, x2, y2 = detection["xyxy"]
            crop = output / "crops" / f"{output_name}__plate_{i:02d}.png"
            save_image(crop, image[y1:y2, x1:x2])
            detection["crop"] = relative(crop, output)
            cv2.rectangle(annotated, (x1, y1), (x2 - 1, y2 - 1), (30, 210, 45), thickness)
            label = f"plate {i} {detection['confidence']:.2f}"
            scale = max(0.5, thickness * 0.3)
            cv2.putText(annotated, label, (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thickness + 2)
            cv2.putText(annotated, label, (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, scale, (30, 210, 45), thickness)
        annotated_path = output / "annotated" / f"{output_name}.jpg"
        save_image(annotated_path, annotated)
        records.append({"name": path.name, "split": split, "original": relative(path.resolve(), output),
                        "annotated": relative(annotated_path, output), "width": image.shape[1], "height": image.shape[0],
                        "elapsed_ms": round(elapsed, 2), "detections": detections})
        print(f"{path.name}: {len(detections)} plates, {elapsed:.1f} ms", flush=True)
    summary = {"model": args.model.name, "provider": session.get_providers(), "threshold": args.conf,
               "image_count": len(records), "images_with_detections": sum(bool(r["detections"]) for r in records),
               "plate_count": sum(len(r["detections"]) for r in records),
               "mean_detection_ms": round(sum(r["elapsed_ms"] for r in records) / len(records), 2),
               "timing": "After one warmup; preprocessing + inference + coordinate conversion; excludes file IO",
               "coordinate_format": "xyxy in original-image pixels, x2/y2 exclusive",
               "note": "Detection count is not accuracy; no OCR performed. See evaluation for ground-truth metrics."}
    if args.evaluation:
        summary["evaluation"] = json.loads(args.evaluation.read_text())
    (output / "detections.json").write_text(json.dumps({"summary": summary, "images": records}, ensure_ascii=False, indent=2) + "\n")
    gallery(records, output, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Preview: {output / 'index.html'}")


if __name__ == "__main__":
    main()
