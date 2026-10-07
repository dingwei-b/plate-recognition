"""Fine-tune PARSeq-Tiny for 20 fixed epochs, reading only the OCR train manifest."""
import argparse
import json
import random
import time
from PIL import Image
from ocr_runtime import ROOT, load_model, sha256, torch, transform
from ocr_loss import PermutationLoss


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--epochs', type=int, default=20)
    parser.add_argument('--batch', type=int, default=16)
    parser.add_argument('--lr', type=float, default=1e-5)
    parser.add_argument('--device', choices=['cpu', 'mps', 'cuda'], default='mps')
    args = parser.parse_args()
    if args.epochs < 1 or args.batch < 1 or args.lr <= 0:
        parser.error('epochs, batch and lr must be positive')
    if args.device == 'mps' and not torch.backends.mps.is_available():
        parser.error('MPS unavailable; use GPU access or --device cpu')
    random.seed(42)
    torch.manual_seed(42)
    torch.set_num_threads(4)
    manifest = ROOT / 'data/ocr/train.json'
    records = json.loads(manifest.read_text())
    assert all(r['split'] == 'train' and r['crop'].startswith('data/ocr/crops/train/') for r in records)
    records = [r for r in records if r['text']]
    assert records, 'No labeled training crops'
    samples = []
    for record in records:
        path = ROOT / record['crop']
        assert sha256(path) == record['crop_sha256'], path
        assert 0 < len(record['text']) <= 25
        with Image.open(path) as image:
            samples.append((transform(image.convert('RGB')), record['text']))
    weights = ROOT / 'models/parseq-tiny-pretrained.pt'
    model, tokenizer = load_model(weights, args.device)
    initial = {n: p.detach().cpu().clone() for n, p in model.named_parameters()}
    loss_fn = PermutationLoss(model, tokenizer)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.0)
    output = ROOT / 'runs/ocr-finetune'
    output.mkdir(parents=True, exist_ok=True)
    config = dict(vars(args), seed=42, train_crops=len(samples), parameters=sum(p.numel() for p in model.parameters()),
                  optimizer='AdamW', weight_decay=0.0, loss='PARSeq permutation cross-entropy (6 permutations)',
                  input_size=[32, 128], augmentation='none; bicubic resize and normalization only',
                  train_manifest_sha256=sha256(manifest), initial_weights_sha256=sha256(weights),
                  checkpoint_selection='last fixed epoch; no validation or test access')
    (output / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
    # Explicit input list makes train/test separation auditable without opening the test set here.
    (output / 'training_inputs.json').write_text(json.dumps(records, indent=2) + '\n')
    print(json.dumps(config), flush=True)
    history, steps = [], 0
    started = time.perf_counter()
    for epoch in range(1, args.epochs + 1):
        model.train()
        epoch_start = time.perf_counter()
        indices = list(range(len(samples)))
        random.shuffle(indices)
        total = 0.0
        for start in range(0, len(indices), args.batch):
            batch = [samples[i] for i in indices[start:start + args.batch]]
            images = torch.stack([s[0] for s in batch]).to(args.device)
            labels = [s[1] for s in batch]
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn.training_step((images, labels), start // args.batch)
            if not torch.isfinite(loss):
                raise RuntimeError(f'Non-finite loss: epoch {epoch}, batch {start}')
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 20.0, error_if_nonfinite=True)
            optimizer.step()
            total += loss.item() * len(batch)
            steps += 1
            if start == 0:
                print(f'Epoch {epoch}/{args.epochs}: first batch loss={loss.item():.4f}', flush=True)
        history.append(dict(epoch=epoch, loss=total / len(samples), seconds=round(time.perf_counter() - epoch_start, 2)))
        (output / 'history.json').write_text(json.dumps(history, indent=2) + '\n')
        torch.save({k: v.detach().cpu() for k, v in model.state_dict().items()}, output / 'last.pt')
        print(json.dumps(history[-1]), flush=True)
    destination = ROOT / 'models/parseq-tiny-finetuned.pt'
    destination.write_bytes((output / 'last.pt').read_bytes())
    changed = sum(not torch.equal(initial[n], p.detach().cpu()) for n, p in model.named_parameters())
    assert changed > 0
    report = dict(completed_epochs=args.epochs, optimizer_steps=steps, train_crops=len(samples),
                  duration_seconds=round(time.perf_counter() - started, 2), changed_parameter_tensors=changed,
                  initial_weights_sha256=sha256(weights), final_weights_sha256=sha256(destination),
                  train_manifest_sha256=sha256(manifest), checkpoint_selection=config['checkpoint_selection'])
    (output / 'training.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
