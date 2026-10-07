# US License Plate Recognition

Detect US license plates with YOLOv9-t and read their numbers with PARSeq-Tiny. The project includes PyTorch training and evaluation scripts, sample data, model weights, and a browser-based comparison of test results.

## Setup

Install [Miniconda](https://docs.conda.io/en/latest/miniconda.html) or Anaconda, then run the following commands from the project directory:

```bash
conda create -n plate-recognition python=3.10 pip -y
conda activate plate-recognition
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

The required YOLOv9 and PARSeq source code is included in `vendor/yolov9` and `vendor/parseq`. Python packages are installed through `requirements.txt`. No TensorFlow or Keras is required.

## View results

Open [results/index.html](results/index.html) in a browser. The saved results work without installing dependencies or running a model. Keep the `data` and `results` folders together in the project directory so image links work.

Alternatively, serve the project directory:

```bash
python -m http.server 8765 --bind 127.0.0.1
```

Visit [http://127.0.0.1:8765/results/index.html](http://127.0.0.1:8765/results/index.html).

The page shows 20 test images with pagination. It compares three detectors (pretrained, fine-tuned, and trained from scratch) and displays original and fine-tuned OCR predictions below each detected plate.

## Run evaluation

Regenerate the detection and OCR comparison using the included weights:

```bash
python compare.py
```

To update only the OCR predictions and metrics:

```bash
python evaluate_ocr.py
```

Both commands update `results/index.html` and `results/detections.json`. Inference runs on the CPU. After changing detector weights, run `python evaluate.py --include-scratch` before rebuilding the comparison.

## Train

The data and labels are included. These commands retrain the models for 20 epochs and rebuild the results:

```bash
python train.py --device cpu --epochs 20
python train.py --device cpu --epochs 20 --scratch
python prepare_ocr.py
python train_ocr.py --device cpu --epochs 20
python evaluate.py --include-scratch
python compare.py
```

Use `--device mps` for training on an Apple GPU. OCR training also supports `--device cuda` with a CUDA-enabled PyTorch installation; the detector training script currently supports CPU and MPS.

Training uses the training split only and saves the final epoch, without selecting a checkpoint using test results. Rerunning training replaces the corresponding weights and logs in `models/` and `runs/`.

## Data and metrics

The fixed split contains 300 training images and 20 test images, sampled with seed 42. OCR uses 277 labeled training plate images. Of the 20 test plates, 17 have confirmed text labels; the other three are displayed but excluded from OCR accuracy.

Detection is evaluated using precision and recall at confidence 0.25 and IoU 0.50. The OCR summary table measures exact plate accuracy on images taken from manually labeled boxes. Per-image OCR results use the boxes predicted by YOLO, so they may differ from the summary table. Text comparison ignores case, spaces, and punctuation, but preserves distinctions such as O/0 and I/1.

This small dataset demonstrates the workflow. Pretraining overlap and similar images across splits have not been ruled out, and results have not been validated on DDOT data.

## Project structure

```text
data/       Images, detection labels, OCR labels, and source records
models/     Pretrained and trained weights, architecture, and provenance
results/    HTML comparison, annotated images, plate images, and predictions
runs/       Training configurations, losses, checkpoints, and test metrics
vendor/     Upstream YOLOv9 and PARSeq implementations
```

## Sources

- Detection model: [open-image-models](https://github.com/ankandrew/open-image-models), using [YOLOv9](https://github.com/ankandrew/yolov9).
- Text recognition model: [PARSeq](https://github.com/baudm/parseq).
- Images: [OpenALPR US benchmark](https://github.com/openalpr/benchmarks/tree/9790ed20d475be1e940a7e2a3f492a97c2ebed9b/endtoend/us) and [Roboflow License Plate Recognition](https://universe.roboflow.com/roboflow-universe-projects/license-plate-recognition-rxg4e), downloaded through its [Kaggle mirror](https://www.kaggle.com/datasets/adilshamim8/license-plate-recognition).

Source revisions and checksums are recorded in `vendor/source.json`, `models/ocr-source.json`, and `data/selection.json`. Upstream code, model weights, and datasets retain their respective licenses.
