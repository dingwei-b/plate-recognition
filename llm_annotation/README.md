# DDOT License Plate Annotation

This local tool filters exact `356x130` DDOT plate images, labels state and plate text with `GLM-5.3-flash`, and supports human correction in a browser.

## Start on macOS

Double-click `start.command`, or run:

```bash
./start.command
```

The launcher finds Conda, installs Miniforge when Conda is unavailable, creates the `DDOT` environment, installs dependencies, starts the local server, and opens the annotation page.

Use **Annotate** for one-image-at-a-time correction. Use **Browse** for a paginated overview with filters, small images, LLM results, and human results.

## Import another date

The default operation moves matching images. Only readable `356x130` images are selected.

```bash
conda run -n DDOT python prepare_images.py /path/to/DDOT/images/YYYYMMDD
```

Use `--copy` to keep the source files.

## Results

The tool stores its working database and exports in `results/`:

- `annotations.sqlite3`
- `annotations.jsonl`
- `annotations.csv`

The API key is stored in the local `.env` file. The key, images, and annotation results are excluded from Git.
