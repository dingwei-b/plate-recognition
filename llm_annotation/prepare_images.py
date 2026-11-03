#!/usr/bin/env python3

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image


REQUIRED_SIZE = (356, 130)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Move exact-size DDOT plate crops into the annotation workspace"
    )
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        default=Path.home() / "Downloads" / "DDOT" / "images" / "20260921",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path(__file__).resolve().parent / "data" / "images",
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy matching files instead of moving them",
    )
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    destination_root = args.destination.expanduser().resolve()
    if not source.is_dir():
        parser.error(f"Source directory does not exist: {source}")

    date_name = source.name
    destination = destination_root / date_name
    destination.mkdir(parents=True, exist_ok=True)

    selected = []
    unreadable = []
    skipped = 0
    for path in sorted(source.iterdir()):
        if not path.is_file():
            continue
        try:
            with Image.open(path) as image:
                image.load()
                size = image.size
        except Exception as exc:
            unreadable.append({"path": str(path), "error": str(exc)})
            continue
        if size != REQUIRED_SIZE:
            skipped += 1
            continue

        target = destination / path.name
        checksum = sha256(path)
        if target.exists():
            if sha256(target) != checksum:
                raise RuntimeError(f"Destination file differs: {target}")
            if not args.copy:
                path.unlink()
        elif args.copy:
            shutil.copy2(path, target)
        else:
            shutil.move(str(path), str(target))

        selected.append(
            {
                "relative_path": target.relative_to(destination_root).as_posix(),
                "width": REQUIRED_SIZE[0],
                "height": REQUIRED_SIZE[1],
                "sha256": checksum,
            }
        )

    manifest_path = destination_root.parent / "manifest.json"
    existing = []
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = {item["relative_path"]: item for item in existing}
    records.update({item["relative_path"]: item for item in selected})
    manifest_path.write_text(
        json.dumps(list(records.values()), indent=2) + "\n", encoding="utf-8"
    )

    action = "Copied" if args.copy else "Moved"
    print(f"{action} {len(selected)} plate crops to {destination}")
    print(f"Skipped {skipped} images whose size was not 356x130")
    print(f"Unreadable files: {len(unreadable)}")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
