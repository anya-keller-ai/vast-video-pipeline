#!/usr/bin/env bash
set -euo pipefail

comfyui_root=${COMFYUI_ROOT:-/opt/ComfyUI}
model_root=${MODEL_ROOT:-/workspace/ComfyUI/models}
manifest_path=${MANIFEST_PATH:-"$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config/models.lock.json"}
check_models=0
check_gpu=0

usage() {
	cat <<'EOF'
Usage: doctor.sh [--models] [--gpu]

  --models  verify required model sizes and SHA-256 checksums
  --gpu     require nvidia-smi and print GPU information
EOF
}

while [[ $# -gt 0 ]]; do
	case "$1" in
	--models)
		check_models=1
		shift
		;;
	--gpu)
		check_gpu=1
		shift
		;;
	-h | --help)
		usage
		exit 0
		;;
	*)
		echo "ERROR: unknown argument: $1" >&2
		usage >&2
		exit 2
		;;
	esac
done

require_command() {
	if ! command -v "$1" >/dev/null 2>&1; then
		echo "ERROR: missing command: $1" >&2
		exit 1
	fi
	echo "OK   command $1"
}

require_command python
require_command ffmpeg
require_command vspipe
require_command sha256sum

if [[ ! -f "$comfyui_root/main.py" ]]; then
	echo "ERROR: ComfyUI is missing at $comfyui_root" >&2
	exit 1
fi
echo "OK   ComfyUI $comfyui_root"

for node in \
	ComfyUI-MultiGPU \
	ComfyUI-KJNodes \
	ComfyUI-MiniMax-H3-Turbo \
	ComfyUI-FlowDenoise \
	ComfyUI-SeedVR2_VideoUpscaler \
	ComfyUI-VideoHelperSuite; do
	if [[ ! -d "$comfyui_root/custom_nodes/$node" ]]; then
		echo "ERROR: missing custom node: $node" >&2
		exit 1
	fi
done
echo "OK   custom nodes"

if [[ "$check_gpu" -eq 1 ]]; then
	require_command nvidia-smi
	nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
fi

if [[ "$check_models" -eq 1 ]]; then
	[[ -f "$manifest_path" ]] || {
		echo "ERROR: missing manifest: $manifest_path" >&2
		exit 1
	}
	python3 - "$manifest_path" "$model_root" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
model_root = Path(sys.argv[2])
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
for model in manifest["models"]:
    if not model.get("required", True):
        continue
    target = model_root / model["target"]
    if not target.is_file():
        raise SystemExit(f"ERROR: missing model {model['id']}: {target}")
    if target.stat().st_size != model["bytes"]:
        raise SystemExit(f"ERROR: byte count mismatch for {model['id']}")
    digest = hashlib.sha256()
    with target.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != model["sha256"]:
        raise SystemExit(f"ERROR: checksum mismatch for {model['id']}")
    print(f"OK   model {model['id']}")
PY
fi

echo "Doctor checks passed."
