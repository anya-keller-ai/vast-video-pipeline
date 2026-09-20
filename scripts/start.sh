#!/usr/bin/env bash
set -euo pipefail

comfyui_root=${COMFYUI_ROOT:-/opt/ComfyUI}
data_root=${DATA_ROOT:-/workspace}
log_path=${COMFYUI_LOG:-"$data_root/comfyui.log"}
pid_path=${COMFYUI_PID:-"$data_root/comfyui.pid"}
port=${COMFYUI_PORT:-8188}
listen_address=${COMFYUI_LISTEN:-127.0.0.1}

if [[ ! -f "$comfyui_root/main.py" ]]; then
	echo "ERROR: ComfyUI is missing at $comfyui_root" >&2
	exit 1
fi

mkdir -p "$data_root/ComfyUI/models" "$data_root/ComfyUI/input" "$data_root/ComfyUI/output" "$data_root/ComfyUI/temp"
if [[ -e "$comfyui_root/models" && ! -L "$comfyui_root/models" ]]; then
	rmdir "$comfyui_root/models" 2>/dev/null || {
		echo "ERROR: $comfyui_root/models is not an empty directory." >&2
		exit 1
	}
fi
if [[ ! -L "$comfyui_root/models" ]]; then
	ln -s "$data_root/ComfyUI/models" "$comfyui_root/models"
fi

if [[ -f "$pid_path" ]]; then
	old_pid=$(cat "$pid_path")
	if kill -0 "$old_pid" 2>/dev/null; then
		echo "ComfyUI is already running with PID $old_pid."
		exit 0
	fi
	rm -f "$pid_path"
fi

args=(
	"$comfyui_root/main.py"
	"--listen" "$listen_address"
	"--port" "$port"
	"--disable-auto-launch"
)
if [[ ${COMFYUI_EXTRA_ARGS:-} != "" ]]; then
	read -r -a extra_args <<<"$COMFYUI_EXTRA_ARGS"
	args+=("${extra_args[@]}")
fi

if [[ ${FOREGROUND:-0} == 1 ]]; then
	exec python "${args[@]}"
fi

nohup python "${args[@]}" >>"$log_path" 2>&1 </dev/null &
echo $! >"$pid_path"
echo "ComfyUI started with PID $(cat "$pid_path") on $listen_address:$port."
