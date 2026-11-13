#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
manifest_path=${MANIFEST_PATH:-"$project_root/config/models.lock.json"}
model_root=${MODEL_ROOT:-/workspace/ComfyUI/models}
staging_root=${STAGING_ROOT:-"$model_root/.staging"}
cache_root=${HF_CACHE_ROOT:-"$model_root/.hf-cache"}
bundle_id=""
if [[ $# -gt 0 ]]; then
	if [[ $# -ne 2 || $1 != "--bundle" || -z "$2" ]]; then
		echo "ERROR: expected optional arguments: --bundle <bundle-id>" >&2
		exit 2
	fi
	bundle_id=$2
fi
bundle_staging_root="$staging_root"
if [[ -n "$bundle_id" ]]; then
	bundle_staging_root="$staging_root/$bundle_id"
fi
lock_path="$model_root/.prepare-models.lock"

mkdir -p "$model_root" "$bundle_staging_root" "$cache_root"
if ! mkdir "$lock_path" 2>/dev/null; then
	echo "ERROR: another model preparation is running or lock is stale: $lock_path" >&2
	exit 1
fi
cleanup() {
	rmdir "$lock_path" 2>/dev/null || true
}
trap cleanup EXIT

command -v hf >/dev/null 2>&1 || {
	echo "WARN: Hugging Face CLI 'hf' is not installed; only direct-URL models can be fetched." >&2
}
command -v sha256sum >/dev/null 2>&1 || {
	echo "ERROR: sha256sum is required." >&2
	exit 1
}

export HF_HUB_DOWNLOAD_TIMEOUT=${HF_HUB_DOWNLOAD_TIMEOUT:-120}
export HF_HUB_DISABLE_XET=${HF_HUB_DISABLE_XET:-0}
export HF_XET_HIGH_PERFORMANCE=${HF_XET_HIGH_PERFORMANCE:-1}
export HF_HUB_DISABLE_PROGRESS_BARS=${HF_HUB_DISABLE_PROGRESS_BARS:-0}
# Hugging Face CLI 1.32 rejects HF_HOME/HF_XET_CACHE together with --local-dir.
unset HF_HOME HF_XET_CACHE

if [[ ! -f "$manifest_path" ]]; then
	echo "ERROR: model manifest does not exist: $manifest_path" >&2
	exit 1
fi

mapfile -t entries < <(
	python3 - "$manifest_path" "$bundle_id" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
bundle_id = sys.argv[2]
models = manifest["models"]
if bundle_id:
    bundles = manifest.get("bundles", [])
    bundle = next((item for item in bundles if item.get("id") == bundle_id), None)
    if bundle is None:
        raise SystemExit(f"Unknown model bundle: {bundle_id}")
    asset_ids = set(bundle["assets"])
    models = [model for model in models if model["id"] in asset_ids]
    if {model["id"] for model in models} != asset_ids:
        raise SystemExit(f"Bundle references unknown assets: {bundle_id}")
else:
    models = [model for model in models if model.get("required", True)]

for model in models:
    fields = (
        model["id"],
        model["repository"],
        model.get("revision", ""),
        model["source"],
        model["target"],
        str(model["bytes"]),
        model["sha256"],
        model.get("url", ""),
    )
    print("\t".join(fields))
PY
)

if [[ ${#entries[@]} -eq 0 ]]; then
	echo "ERROR: selected model bundle contains no files." >&2
	exit 1
fi

available_kb=$(df -Pk "$model_root" | awk 'NR == 2 { print $4 }')
missing_bytes=0
for entry in "${entries[@]}"; do
	IFS=$'\t' read -r _ _ _ _ target expected_bytes _ <<<"$entry"
	destination="$model_root/$target"
	if [[ ! -f "$destination" ]] || [[ $(stat -c '%s' "$destination") -ne "$expected_bytes" ]]; then
		missing_bytes=$((missing_bytes + expected_bytes))
	fi
done
minimum_free_bytes=${MIN_FREE_BYTES:-21474836480}
required_kb=$(((missing_bytes + minimum_free_bytes) / 1024))
if [[ "$available_kb" -lt "$required_kb" ]]; then
	echo "ERROR: insufficient disk space. Need about $((required_kb / 1024 / 1024)) GiB including headroom." >&2
	exit 1
fi

for entry in "${entries[@]}"; do
	IFS=$'\t' read -r model_id repository revision source target expected_bytes expected_sha256 download_url <<<"$entry"
	destination="$model_root/$target"
	staging_path="$bundle_staging_root/$source"
	staging_dir=$(dirname "$staging_path")
	mkdir -p "$staging_dir" "$(dirname "$destination")"

	if [[ -L "$destination" ]]; then
		echo "ERROR: model target is a symbolic link; remove it manually: $destination" >&2
		exit 1
	fi
	if [[ -e "$destination" ]]; then
		if [[ ! -f "$destination" ]] || [[ $(stat -c '%s' "$destination") -ne "$expected_bytes" ]]; then
			echo "ERROR: model target exists with an invalid size; remove it manually: $destination" >&2
			exit 1
		fi
		echo "VERIFY $model_id"
		actual_sha256=$(sha256sum "$destination" | awk '{ print $1 }')
		if [[ "$actual_sha256" == "$expected_sha256" ]]; then
			echo "OK   $model_id"
			continue
		fi
		echo "ERROR: model target has an invalid checksum; remove it manually: $destination" >&2
		exit 1
	fi

	echo "GET  $model_id"
	if [[ -n "$download_url" ]]; then
		curl --fail --location --retry 3 --retry-delay 5 --output "$staging_path" "$download_url"
	else
		hf download "$repository" "$source" \
			--revision "$revision" \
			--local-dir "$bundle_staging_root"
	fi

	if [[ ! -f "$staging_path" ]]; then
		echo "ERROR: download did not produce $staging_path" >&2
		exit 1
	fi
	actual_bytes=$(stat -c '%s' "$staging_path")
	if [[ "$actual_bytes" -ne "$expected_bytes" ]]; then
		echo "ERROR: byte count mismatch for $model_id: $actual_bytes != $expected_bytes" >&2
		exit 1
	fi
	echo "VERIFY $model_id"
	actual_sha256=$(sha256sum "$staging_path" | awk '{ print $1 }')
	if [[ "$actual_sha256" != "$expected_sha256" ]]; then
		echo "ERROR: checksum mismatch for $model_id; partial/staged file is preserved" >&2
		exit 1
	fi
	if [[ -e "$destination" || -L "$destination" ]]; then
		echo "ERROR: model target appeared during download; preserving staged file: $destination" >&2
		exit 1
	fi
	mv "$staging_path" "$destination"
	echo "DONE $model_id"
done

echo "Selected model bundle is ready in $model_root."
