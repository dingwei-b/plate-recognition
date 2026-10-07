"""Train all 300 images from pretrained or random weights; never read test data."""
import argparse
from copy import deepcopy
import hashlib
import json
import random
import time

from yolo_runtime import ROOT, load_model, torch
import cv2
import numpy as np
from detect import prepare
from utils.loss_tal import ComputeLoss
from utils.torch_utils import smart_optimizer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--lr", type=float, default=0.0001)
    parser.add_argument("--device", choices=["cpu", "mps"], default="mps")
    parser.add_argument("--scratch", action="store_true", help="Random initialization, no pretrained weights")
    args = parser.parse_args()
    if args.epochs < 1 or args.batch < 1 or args.lr <= 0:
        parser.error("epochs, batch and lr must be positive")
    if args.device == "mps" and not torch.backends.mps.is_available():
        parser.error("MPS unavailable; run with GPU access or explicitly select --device cpu")
    random.seed(42)
    np.random.seed(42)
    torch.manual_seed(42)
    torch.set_num_threads(4)
    output = ROOT / "runs" / ("scratch" if args.scratch else "finetune")
    output.mkdir(parents=True, exist_ok=True)
    weights = ROOT / "models/yolo-v9-t-384-pretrained.pt"
    if args.scratch:
        from models.yolo import DetectionModel
        architecture = json.loads((ROOT / "models/yolo-v9-t-plate.json").read_text())
        model = DetectionModel(architecture, ch=3, nc=1).to(args.device)
        model.names = {0: "Vehicle registration plate"}
        # Preserve evidence of random initialization before any optimizer update.
        torch.save({"model": deepcopy(model).cpu().eval(), "epoch": 0}, output / "initial.pt")
    else:
        model = load_model(weights, args.device)
    for name, param in model.named_parameters():
        # DFL projection is a fixed integral, not a trainable convolution.
        param.requires_grad_(".dfl." not in name)
    model.hyp = {"cls_pw": 1.0, "fl_gamma": 0.0, "label_smoothing": 0.0}
    loss_fn = ComputeLoss(model)
    optimizer = smart_optimizer(model, "AdamW", args.lr, 0.9, 0.0005)
    samples = []
    for path in sorted((ROOT / "data/images/train").glob("*.jpg")):
        image = cv2.imread(str(path))
        if image is None:
            raise ValueError(f"Cannot read {path}")
        tensor, ratio, dx, dy = prepare(image, 384)
        label_path = ROOT / "data/labels/train" / (path.stem + ".txt")
        labels = np.loadtxt(label_path, dtype=np.float32).reshape(-1, 5)
        labels[:, 1] = (labels[:, 1] * image.shape[1] * ratio + dx) / 384
        labels[:, 2] = (labels[:, 2] * image.shape[0] * ratio + dy) / 384
        labels[:, 3] *= image.shape[1] * ratio / 384
        labels[:, 4] *= image.shape[0] * ratio / 384
        samples.append((torch.from_numpy(tensor[0]), torch.from_numpy(labels)))
    assert len(samples) == 300, f"Expected 300 training images, got {len(samples)}"
    config = dict(vars(args), seed=42, image_size=384, train_images=len(samples),
                  optimizer="AdamW", augmentation="none (letterbox only)",
                  selection_sha256=hashlib.sha256((ROOT / "data/selection.json").read_bytes()).hexdigest(),
                  initialization="random" if args.scratch else "pretrained",
                  initial_weights_sha256=hashlib.sha256((output / "initial.pt" if args.scratch else weights).read_bytes()).hexdigest(),
                  architecture_sha256=hashlib.sha256((ROOT / "models/yolo-v9-t-plate.json").read_bytes()).hexdigest(),
                  checkpoint_selection="last fixed epoch; no validation/test access")
    (output / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    initial = {n: p.detach().cpu().clone() for n, p in model.named_parameters() if p.requires_grad}
    history, steps = [], 0
    started = time.perf_counter()
    print(json.dumps(config), flush=True)
    for epoch in range(args.epochs):
        epoch_started = time.perf_counter()
        model.train()
        indices = list(range(len(samples)))
        random.shuffle(indices)
        total = np.zeros(3)
        for offset in range(0, len(indices), args.batch):
            batch = [samples[i] for i in indices[offset:offset + args.batch]]
            images = torch.stack([x[0] for x in batch]).to(args.device)
            targets = torch.cat([torch.cat((torch.full((len(y), 1), i), y), 1)
                                 for i, (_, y) in enumerate(batch)]).to(args.device)
            optimizer.zero_grad(set_to_none=True)
            loss, parts = loss_fn(model(images), targets)
            loss = loss / len(batch)
            if not torch.isfinite(loss):
                raise RuntimeError(f"Nonfinite loss at epoch {epoch + 1}, batch {offset}")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 10.0, error_if_nonfinite=True)
            optimizer.step()
            steps += 1
            total += parts.detach().cpu().numpy() * len(batch)
            if offset == 0:
                print(f"Epoch {epoch + 1}/{args.epochs}: first batch loss={loss.item():.4f}", flush=True)
        row = dict(epoch=epoch + 1, box_loss=float(total[0] / len(samples)),
                   cls_loss=float(total[1] / len(samples)), dfl_loss=float(total[2] / len(samples)),
                   seconds=round(time.perf_counter() - epoch_started, 2), optimizer_steps=steps)
        history.append(row)
        (output / "history.json").write_text(json.dumps(history, indent=2) + "\n")
        checkpoint = {"model": deepcopy(model).cpu().eval(), "epoch": epoch + 1, "config": config,
                      "optimizer_steps": steps}
        torch.save(checkpoint, output / "last.pt")
        print(json.dumps(row), flush=True)
    changed = sum(not torch.equal(initial[n], p.detach().cpu()) for n, p in model.named_parameters() if n in initial)
    assert changed > 0, "No weights changed"
    final = ROOT / "models" / ("yolo-v9-t-384-scratch.pt" if args.scratch else "yolo-v9-t-384-finetuned.pt")
    torch.save(checkpoint, final)
    report = dict(config, completed_epochs=len(history), optimizer_steps=steps,
                  training_image_visits=len(samples) * len(history), changed_parameter_tensors=changed,
                  seconds=round(time.perf_counter() - started, 2),
                  final_weights_sha256=hashlib.sha256(final.read_bytes()).hexdigest())
    (output / "training.json").write_text(json.dumps(report, indent=2) + "\n")
    print("Training complete: " + json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
