"""Build a compact three-model gallery using only the fixed 20 test images."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote

from detect import ROOT, gallery, relative


def main():
    evaluation = json.loads((ROOT / "runs/comparison.json").read_text())
    output = ROOT / "results"
    records, summaries = [], {}
    for key, label, filename in [("baseline", "Pretrained", "yolo-v9-t-384-pretrained.pt"),
                                  ("finetuned", "Fine-tuned", "yolo-v9-t-384-finetuned.pt"),
                                  ("scratch", "From scratch", "yolo-v9-t-384-scratch.pt")]:
        weights = ROOT / "models" / filename
        assert hashlib.sha256(weights.read_bytes()).hexdigest() == evaluation[key]["sha256"], "Re-run evaluate.py for current weights"
        subdir = output / key
        subprocess.run([sys.executable, str(ROOT / "detect.py"), "--input", str(ROOT / "data/images/test"),
                        "--model", str(weights), "--output", str(subdir), "--conf", "0.25"], check=True)
        run = json.loads((subdir / "detections.json").read_text())
        assert len(run["images"]) == 20 and all(r["split"] == "test" for r in run["images"])
        summaries[key] = run["summary"]
        for index, item in enumerate(run["images"]):
            rebase = lambda value: relative((subdir / unquote(value)).resolve(), output)
            if key == "baseline":
                records.append(dict(name=item["name"], split="test", original=rebase(item["original"]), predictions=[]))
            assert records[index]["name"] == item["name"], "Image ordering mismatch"
            predictions = [dict(d, crop=rebase(d["crop"])) for d in item["detections"]]
            records[index]["predictions"].append(dict(key=key, label=label, annotated=rebase(item["annotated"]),
                                                      elapsed_ms=item["elapsed_ms"], detections=predictions))
    summary = dict(comparison=True, image_count=20, threshold=0.25, models=summaries, evaluation=evaluation)
    run = dict(summary=summary, images=records)
    if (ROOT / 'models/parseq-tiny-finetuned.pt').is_file():
        from evaluate_ocr import enrich
        enrich(run)
    (output / "detections.json").write_text(json.dumps(run, indent=2) + "\n")
    gallery(records, output, summary)
    print(f"Three-model comparison: {output / 'index.html'}")


if __name__ == "__main__":
    main()
