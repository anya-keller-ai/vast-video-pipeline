#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
manifest_path=${MANIFEST_PATH:-"$project_root/config/models.lock.json"}
model_root=${MODEL_ROOT:-/workspace/ComfyUI/models}
staging_root=${STAGING_ROOT:-"$model_root/.staging"}
cache_root=${HF_CACHE_ROOT:-"$model_root/.hf-cache"}
lock_path="$model_root/.prepare-models.lock"

mkdir -p "$model_root" "$staging_root" "$cache_root"
if ! mkdir "$lock_path" 2>/dev/null; then
	echo "ERROR: another model preparation is running or lock is stale: $lock_path" >&2
	exit 1
fi
cleanup() {
	rmdir "$lock_path" 2>/dev/null || true
}
trap cleanup EXIT

command -v hf >/dev/null 2>&1 || {
	echo "ERROR: Hugging Face CLI 'hf' is not installed." >&2
	exit 1
}
command -v sha256sum >/dev/null 2>&1 || {
	echo "ERROR: sha256sum is required." >&2
	exit 1
}

export HF_HUB_DOWNLOAD_TIMEOUT=${HF_HUB_DOWNLOAD_TIMEOUT:-120}
export HF_XET_CACHE=${HF_XET_CACHE:-"$cache_root/xet"}
mkdir -p "$HF_XET_CACHE"

if [[ ! -f "$manifest_path" ]]; then
	echo "ERROR: model manifest does not exist: $manifest_path" >&2
	exit 1
fi

mapfile -t entries < <(
	python3 - "$manifest_path" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for model in manifest["models"]:
    if model.get("required", True):
        fields = (
            model["id"],
            model["repository"],
            model["source"],
            model["target"],
            str(model["bytes"]),
            model["sha256"],
        )
        print("\t".join(fields))
PY
)

if [[ ${#entries[@]} -eq 0 ]]; then
	echo "ERROR: model manifest contains no required models." >&2
	exit 1
fi

available_kb=$(df -Pk "$model_root" | awk 'NR == 2 { print $4 }')
missing_bytes=0
for entry in "${entries[@]}"; do
	IFS=$'\t' read -r _ _ _ target expected_bytes _ <<<"$entry"
	destination="$model_root/$target"
	if [[ ! -f "$destination" ]] || [[ $(stat -c '%s' "$destination") -ne "$expected_bytes" ]]; then
		missing_bytes=$((missing_bytes + expected_bytes))
	fi
done
required_kb=$(((missing_bytes + 20 * 1024 * 1024 * 1024) / 1024))
if [[ "$available_kb" -lt "$required_kb" ]]; then
	echo "ERROR: insufficient disk space. Need about $((required_kb / 1024 / 1024)) GiB including headroom." >&2
	exit 1
fi

for entry in "${entries[@]}"; do
	IFS=$'\t' read -r model_id repository source target expected_bytes expected_sha256 <<<"$entry"
	destination="$model_root/$target"
	staging_path="$staging_root/$source"
	staging_dir=$(dirname "$staging_path")
	mkdir -p "$staging_dir" "$(dirname "$destination")"

	if [[ -f "$destination" ]] && [[ $(stat -c '%s' "$destination") -eq "$expected_bytes" ]]; then
		actual_sha256=$(sha256sum "$destination" | awk '{ print $1 }')
		if [[ "$actual_sha256" == "$expected_sha256" ]]; then
			echo "OK   $model_id"
			continue
		fi
		echo "WARN $model_id has an invalid checksum; downloading again" >&2
		rm -f "$destination"
	fi

	echo "GET  $model_id"
	hf download "$repository" "$source" \
		--local-dir "$staging_root" \
		--cache-dir "$cache_root"

	if [[ ! -f "$staging_path" ]]; then
		echo "ERROR: Hugging Face did not produce $staging_path" >&2
		exit 1
	fi
	actual_bytes=$(stat -c '%s' "$staging_path")
	if [[ "$actual_bytes" -ne "$expected_bytes" ]]; then
		echo "ERROR: byte count mismatch for $model_id: $actual_bytes != $expected_bytes" >&2
		exit 1
	fi
	actual_sha256=$(sha256sum "$staging_path" | awk '{ print $1 }')
	if [[ "$actual_sha256" != "$expected_sha256" ]]; then
		echo "ERROR: checksum mismatch for $model_id" >&2
		exit 1
	fi
	mv -f "$staging_path" "$destination"
	echo "DONE $model_id"
done

rm -rf "$staging_root"
echo "All required models are ready in $model_root."
