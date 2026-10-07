"""Load the pinned original YOLOv9 implementation with project-local dependencies."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("MPLCONFIGDIR", "/tmp/plate-mpl")
os.environ.setdefault("YOLOv5_AUTOINSTALL", "false")
sys.path[:0] = [str(ROOT), str(ROOT / "vendor/python"), str(ROOT / "vendor/yolov9")]

import numpy as np
import torch
from utils.general import non_max_suppression


def load_model(path, device="cpu"):
    # Only use our checkpoint or the trusted, documented upstream checkpoint.
    model = torch.load(path, map_location="cpu", weights_only=False)["model"].float()
    return model.to(device)


class TorchDetector:
    def __init__(self, path):
        torch.set_num_threads(4)
        self.model = load_model(path).eval()

    @torch.inference_mode()
    def boxes(self, image, threshold=0.25):
        from detect import prepare
        tensor, ratio, dx, dy = prepare(image, 384)
        predictions = self.model(torch.from_numpy(tensor))
        boxes = non_max_suppression(predictions, conf_thres=threshold, iou_thres=0.45, max_det=100)[0]
        boxes[:, [0, 2]] = ((boxes[:, [0, 2]] - dx) / ratio).clamp(0, image.shape[1])
        boxes[:, [1, 3]] = ((boxes[:, [1, 3]] - dy) / ratio).clamp(0, image.shape[0])
        return boxes.cpu().numpy()

    def predict(self, image, threshold):
        rows = self.boxes(image, threshold)
        results = []
        for x1, y1, x2, y2, score, cls in rows:
            bounds = [int(np.floor(x1)), int(np.floor(y1)), int(np.ceil(x2)), int(np.ceil(y2))]
            if bounds[2] > bounds[0] and bounds[3] > bounds[1]:
                results.append({"xyxy": bounds, "confidence": round(float(score), 5)})
        return results

    def get_providers(self):
        return ["PyTorch CPU"]
