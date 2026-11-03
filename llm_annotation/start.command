#!/bin/bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_NAME="DDOT"
MINIFORGE_PREFIX="${HOME}/miniforge3"
PORT="${PORT:-8767}"

cd "$APP_DIR"

find_conda() {
  if command -v conda >/dev/null 2>&1; then
    command -v conda
  elif [ -x "$MINIFORGE_PREFIX/bin/conda" ]; then
    printf '%s\n' "$MINIFORGE_PREFIX/bin/conda"
  elif [ -x "${HOME}/miniconda3/bin/conda" ]; then
    printf '%s\n' "${HOME}/miniconda3/bin/conda"
  else
    return 1
  fi
}

if ! CONDA_BIN="$(find_conda)"; then
  if [ "$(uname -s)" != "Darwin" ]; then
    echo "Automatic Miniforge installation is supported on macOS only."
    exit 1
  fi
  case "$(uname -m)" in
    arm64) INSTALLER="Miniforge3-MacOSX-arm64.sh" ;;
    x86_64) INSTALLER="Miniforge3-MacOSX-x86_64.sh" ;;
    *) echo "Unsupported Mac architecture: $(uname -m)"; exit 1 ;;
  esac
  INSTALLER_PATH="${TMPDIR:-/tmp}/${INSTALLER}"
  echo "Miniforge was not found. Downloading the official installer..."
  curl -fL "https://github.com/conda-forge/miniforge/releases/latest/download/${INSTALLER}" -o "$INSTALLER_PATH"
  bash "$INSTALLER_PATH" -b -p "$MINIFORGE_PREFIX"
  CONDA_BIN="$MINIFORGE_PREFIX/bin/conda"
fi

if ! "$CONDA_BIN" env list | awk '{print $1}' | grep -qx "$ENV_NAME"; then
  echo "Creating Conda environment: $ENV_NAME"
  "$CONDA_BIN" create -n "$ENV_NAME" python=3.11 pip -y
fi

REQ_HASH="$(shasum -a 256 requirements.txt | awk '{print $1}')"
STAMP_FILE=".requirements-${REQ_HASH}"
DEPENDENCIES_READY=false
if "$CONDA_BIN" run -n "$ENV_NAME" python -c "import fastapi, uvicorn, dotenv, requests, PIL" >/dev/null 2>&1; then
  DEPENDENCIES_READY=true
fi

if [ ! -f "$STAMP_FILE" ] || [ "$DEPENDENCIES_READY" != true ]; then
  echo "Installing annotation dependencies..."
  "$CONDA_BIN" run -n "$ENV_NAME" python -m pip install --upgrade pip
  "$CONDA_BIN" run -n "$ENV_NAME" python -m pip install -r requirements.txt
  rm -f .requirements-*
  touch "$STAMP_FILE"
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env. Add AUTODL_API_KEY and run this file again."
  exit 1
fi

echo "Starting DDOT annotation page at http://127.0.0.1:${PORT}"
"$CONDA_BIN" run -n "$ENV_NAME" python -m uvicorn app:app --host 127.0.0.1 --port "$PORT" &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT INT TERM

for _ in $(seq 1 60); do
  if curl --noproxy '*' -fsS "http://127.0.0.1:${PORT}/api/config" >/dev/null 2>&1; then
    open "http://127.0.0.1:${PORT}"
    wait "$SERVER_PID"
    exit $?
  fi
  sleep 1
done

echo "The server did not start within 60 seconds."
exit 1
