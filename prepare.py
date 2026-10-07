"""Download a reproducible, small US plate sample and a pretrained YOLO detector."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import random
import subprocess

ROOT = Path(__file__).resolve().parent
REPO = "https://github.com/openalpr/benchmarks"
MODEL_URL = "https://github.com/ankandrew/open-image-models/releases/download/assets/yolo-v9-t-384-license-plates-end2end.onnx"


def download(url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    subprocess.run(["curl", "-fsSL", "--retry", "3", "--connect-timeout", "20",
                    "--max-time", "180", url, "-o", str(temporary)], check=True)
    temporary.replace(path)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=20)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count must be positive")
    data = ROOT / "data/demo"
    manifest_path = data / "sources.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if len(manifest["images"]) != args.count:
            parser.error("Existing sample has a different count; keep this fixed sample unchanged.")
    else:
        revision = json.loads(subprocess.check_output([
            "curl", "-fsSL", "--retry", "3", "--max-time", "30",
            "https://api.github.com/repos/openalpr/benchmarks/commits/master"]))["sha"]
        entries = json.loads(subprocess.check_output([
            "curl", "-fsSL", "--retry", "3", "--max-time", "30",
            f"https://api.github.com/repos/openalpr/benchmarks/contents/endtoend/us?ref={revision}"]))
        images = sorted((e for e in entries if e["name"].lower().endswith(".jpg")), key=lambda e: e["name"])
        chosen = sorted(random.Random(42).sample(images, min(args.count, len(images))), key=lambda e: e["name"])
        manifest = {"repository": REPO, "revision": revision, "subset": "endtoend/us",
                    "seed": 42, "purpose": "Small public smoke-test sample, not an independent accuracy benchmark",
                    "images": [{"name": e["name"], "git_blob_sha1": e["sha"], "url": e["download_url"]} for e in chosen]}
        data.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    def fetch(entry):
        target = data / "images" / entry["name"]
        if not target.exists():
            download(entry["url"], target)
        content = target.read_bytes()
        digest = hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
        if digest != entry["git_blob_sha1"]:
            raise ValueError(f"Source checksum mismatch: {target}")
        annotation = data / "annotations" / (target.stem + ".txt")
        if not annotation.exists():
            download(entry["url"].rsplit(".", 1)[0] + ".txt", annotation)
        print(f"Downloaded/verified {target.name}", flush=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, manifest["images"]))
    download(f"https://raw.githubusercontent.com/openalpr/benchmarks/{manifest['revision']}/LICENSE", data / "LICENSE.upstream")
    model = ROOT / "models" / "yolo-v9-t-384.onnx"
    if not model.exists():
        print("Downloading YOLOv9-t plate detector...", flush=True)
        download(MODEL_URL, model)
    (model.parent / "source.json").write_text(json.dumps({
        "url": MODEL_URL, "repository": "https://github.com/ankandrew/open-image-models",
        "sha256": hashlib.sha256(model.read_bytes()).hexdigest(), "bytes": model.stat().st_size,
        "note": "Pretrained license-plate detector, not a generic COCO detector. Consult upstream model terms before redistribution."
    }, indent=2) + "\n")
    print(f"Ready: {len(manifest['images'])} images; model {model.stat().st_size / 1024**2:.1f} MiB")


if __name__ == "__main__":
    main()
