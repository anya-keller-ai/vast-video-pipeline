# syntax=docker/dockerfile:1.10

FROM pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime@sha256:c16f4c749e2d9e96878875cdf6cc45cddda1d1a36fddd371dd6f2360f1b6e2a2

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG COMFYUI_COMMIT=5ba116a40f1944f64e2e4a8ace826656e6293bf4
ARG MULTIGPU_COMMIT=b51c99a525e9607e43545ee2a8b7694c74a4775a
ARG KJ_NODES_COMMIT=d3cfe21625e5170126ce06fbfcfe1d88108688c3
ARG H3_TURBO_COMMIT=4274783a23afcfdbea3b4876cb79effd6c510785
ARG FLOW_DENOISE_COMMIT=2748fb42a883dceefb7e667f8521e6a138d6817d
ARG SEEDVR2_COMMIT=4490bd1f482e026674543386bb2a4d176da245b9
ARG VIDEO_HELPER_COMMIT=4d907bee61e92c2e65af3bd6383a4e4d356126d1
ARG HUGGINGFACE_HUB_VERSION=1.32.0
ARG PYDANTIC_VERSION=2.12.5
ARG PYTHON_VERSION=3.12

ENV DEBIAN_FRONTEND=noninteractive \
    COMFYUI_ROOT=/opt/ComfyUI \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Package versions are resolved by the pinned base-image snapshot.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        bash \
        build-essential \
        ca-certificates \
        curl \
        ffmpeg \
        git \
        libfftw3-3 \
        libfftw3-dev \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

RUN /opt/conda/bin/conda install --yes "python=${PYTHON_VERSION}" \
    && /opt/conda/bin/conda clean --all --yes

RUN python -m pip install \
        --no-cache-dir \
        "huggingface_hub==${HUGGINGFACE_HUB_VERSION}" \
        "vapoursynth==80" \
        "vapoursynth-bm3d==10.1" \
        "vapoursynth-bestsource==22" \
        "sageattention==1.0.6"

# `local` is intentional because this function runs in Bash.
# hadolint ignore=SC3043
RUN set -eux; \
    clone_at() { \
        local repository="$1"; \
        local commit="$2"; \
        local destination="$3"; \
        git clone --filter=blob:none "$repository" "$destination"; \
        git -C "$destination" fetch --depth=1 origin "$commit"; \
        git -C "$destination" checkout --detach "$commit"; \
        git -C "$destination" config --unset core.sparseCheckout || true; \
        rm -rf "$destination/.git"; \
    }; \
    clone_at https://github.com/comfyanonymous/ComfyUI.git "$COMFYUI_COMMIT" /opt/ComfyUI; \
    clone_at https://github.com/pollockjj/ComfyUI-MultiGPU.git "$MULTIGPU_COMMIT" /opt/ComfyUI/custom_nodes/ComfyUI-MultiGPU; \
    clone_at https://github.com/kijai/ComfyUI-KJNodes.git "$KJ_NODES_COMMIT" /opt/ComfyUI/custom_nodes/ComfyUI-KJNodes; \
    clone_at https://github.com/Larryvrh/ComfyUI-MiniMax-H3-Turbo.git "$H3_TURBO_COMMIT" /opt/ComfyUI/custom_nodes/ComfyUI-MiniMax-H3-Turbo; \
    clone_at https://github.com/AIMZ-GFX/ComfyUI-FlowDenoise.git "$FLOW_DENOISE_COMMIT" /opt/ComfyUI/custom_nodes/ComfyUI-FlowDenoise; \
    clone_at https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler.git "$SEEDVR2_COMMIT" /opt/ComfyUI/custom_nodes/ComfyUI-SeedVR2_VideoUpscaler; \
    clone_at https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git "$VIDEO_HELPER_COMMIT" /opt/ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite

RUN python -m pip install --no-cache-dir -r /opt/ComfyUI/requirements.txt \
    && python -m pip install --no-cache-dir -r /opt/ComfyUI/custom_nodes/ComfyUI-KJNodes/requirements.txt \
    && python -m pip install --no-cache-dir -r /opt/ComfyUI/custom_nodes/ComfyUI-SeedVR2_VideoUpscaler/requirements.txt \
    && python -m pip install --no-cache-dir "pydantic==${PYDANTIC_VERSION}" \
    && python -m pip check

COPY config /opt/vast/config
COPY scripts /opt/vast/scripts
COPY postprocess /opt/vast/postprocess
COPY workflows /opt/vast/workflows
COPY README.md /opt/vast/README.md
COPY CONTEXT.md /opt/vast/CONTEXT.md

RUN chmod +x /opt/vast/scripts/*.sh \
    && mkdir -p /workspace/ComfyUI/models /workspace/ComfyUI/input /workspace/ComfyUI/output /workspace/ComfyUI/temp \
    && rm -rf /opt/ComfyUI/models \
    && ln -s /workspace/ComfyUI/models /opt/ComfyUI/models \
    && python -m compileall -q /opt/vast/postprocess

EXPOSE 8188
WORKDIR /opt/ComfyUI
ENTRYPOINT ["/opt/vast/scripts/start.sh"]
