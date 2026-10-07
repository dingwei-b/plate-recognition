"""Compare pretrained and final checkpoints on the same 20 test images, after training."""
import argparse
import hashlib
import json

from yolo_runtime import ROOT, TorchDetector, torch
import cv2
import numpy as np
from utils.metrics import ap_per_class, box_iou


def match(boxes, labels, thresholds):
    """One-to-one, descending-IoU matching, following YOLOv9 val.process_batch."""
    correct = np.zeros((len(boxes), len(thresholds)), dtype=bool)
    iou = box_iou(torch.from_numpy(labels[:, 1:]), torch.from_numpy(boxes[:, :4])).numpy()
    for column, threshold in enumerate(thresholds):
        gt, pred = np.where(iou >= threshold)
        if not len(gt):
            continue
        candidates = np.column_stack((gt, pred, iou[gt, pred]))
        candidates = candidates[candidates[:, 2].argsort()[::-1]]
        candidates = candidates[np.unique(candidates[:, 1], return_index=True)[1]]
        candidates = candidates[np.unique(candidates[:, 0], return_index=True)[1]]
        correct[candidates[:, 1].astype(int), column] = True
    return correct


def evaluate(path):
    detector = TorchDetector(path)
    stats, details = [], []
    total_gt = total_pred = total_tp = 0
    paths = sorted((ROOT / "data/images/test").glob("*.jpg"))
    assert len(paths) == 20
    for image_path in paths:
        image = cv2.imread(str(image_path))
        labels = np.loadtxt(ROOT / "data/labels/test" / (image_path.stem + ".txt"), dtype=np.float32).reshape(-1, 5)
        xywh = labels[:, 1:].copy() * np.array([image.shape[1], image.shape[0]] * 2)
        labels[:, 1:3] = xywh[:, :2] - xywh[:, 2:] / 2
        labels[:, 3:5] = xywh[:, :2] + xywh[:, 2:] / 2
        boxes = detector.boxes(image, threshold=0.001)
        correct = match(boxes, labels, np.linspace(0.5, 0.95, 10))
        stats.append((correct, boxes[:, 4], boxes[:, 5], labels[:, 0]))
        visible = boxes[boxes[:, 4] >= 0.25]
        tp = int(match(visible, labels, [0.5]).sum())
        total_gt += len(labels)
        total_pred += len(visible)
        total_tp += tp
        details.append(dict(image=image_path.name, ground_truth=len(labels), predictions=len(visible), true_positives=tp))
    arrays = [np.concatenate(parts, axis=0) for parts in zip(*stats)]
    ap = ap_per_class(*arrays, names={0: "plate"})[5]
    return dict(model=path.name, images=len(paths), ground_truth_boxes=total_gt,
                predicted_boxes=total_pred, true_positives=total_tp,
                false_positives=total_pred - total_tp, false_negatives=total_gt - total_tp,
                precision=total_tp / max(total_pred, 1), recall=total_tp / max(total_gt, 1),
                map50=float(ap[:, 0].mean()), map50_95=float(ap.mean()), per_image=details)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-scratch", action="store_true", help="Compare all three models")
    args = parser.parse_args()
    report_path = ROOT / "runs/finetune/training.json"
    if not report_path.is_file():
        raise RuntimeError("Complete train.py before running the held-out test comparison")
    training = json.loads(report_path.read_text())
    results = dict(test_images=20, train_images=300, epochs=training["completed_epochs"],
                   checkpoint_selection="last fixed epoch, no test-based checkpoint selection",
                   thresholds={"precision_recall_confidence": 0.25, "precision_recall_iou": 0.5,
                               "ap_min_confidence": 0.001, "nms_iou": 0.45},
                   note="Small workflow test; pretraining overlap and similar images across splits are possible.")
    models = [("baseline", "yolo-v9-t-384-pretrained.pt"), ("finetuned", "yolo-v9-t-384-finetuned.pt")]
    if args.include_scratch:
        scratch = json.loads((ROOT / "runs/scratch/training.json").read_text())
        for key in ["selection_sha256", "completed_epochs", "batch", "lr", "image_size", "seed", "optimizer", "augmentation"]:
            assert training[key] == scratch[key], f"Different training conditions: {key}"
        assert scratch["initialization"] == "random"
        models.append(("scratch", "yolo-v9-t-384-scratch.pt"))
        results["scratch_initialization"] = "random, architecture only; no pretrained weights loaded"
    for key, filename in models:
        if key != "baseline":
            report = training if key == "finetuned" else scratch
            assert hashlib.sha256((ROOT / "models" / filename).read_bytes()).hexdigest() == report["final_weights_sha256"], "Checkpoint does not match completed training report"
        results[key] = evaluate(ROOT / "models" / filename)
        results[key]["sha256"] = hashlib.sha256((ROOT / "models" / filename).read_bytes()).hexdigest()
        print(key + ": " + json.dumps({k: v for k, v in results[key].items() if k != "per_image"}), flush=True)
    destination = ROOT / ("runs/comparison.json" if args.include_scratch else "runs/finetune/evaluation.json")
    destination.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
