export type ModelAssetStatus = 'waiting' | 'downloading' | 'verifying' | 'ready' | 'invalid'
export type ModelBundlePhase = 'waiting' | 'downloading' | 'verifying' | 'ready' | 'error'

export interface ModelAsset {
    id: string
    repository: string
    revision: string | null
    source: string
    target: string
    bytes: number
    sha256: string
    format: string
    loader: string
    status: ModelAssetStatus
    progress: number
    downloadedBytes: number
    storageBytes: number
    error: string | null
    shared: boolean
}

export interface ModelBundle {
    id: string
    title: string
    runtime: string
    workflows: string[]
    enabled: boolean
    blockedReason: string | null
    executionEnabled: boolean
    executionBlockedReason: string | null
    phase: ModelBundlePhase
    assets: ModelAsset[]
    totalBytes: number
    readyBytes: number
    pendingBytes: number
    downloadProgress: number
    freeDiskBytes: number
    diskRequiredBytes: number
    diskSufficient: boolean
    canDownload: boolean
    canDelete: boolean
    deleteBlockedReason: string | null
    currentMessage: string | null
    error: string | null
}

export interface ModelBundlesSnapshot {
    bundles: ModelBundle[]
    activeDownloadBundleId: string | null
    freeDiskBytes: number
    minimumFreeBytes: number
}

export interface ModelDownloadProgressSample {
    timestampMs: number
    transferredBytes: number
}

export interface ModelDownloadTelemetry {
    bytesPerSecond: number | null
    secondsRemaining: number | null
}

export interface BundleDeleteResult {
    bundleId: string
    deletedFiles: string[]
    preservedSharedAssets: string[]
    freedBytes: number
}

export async function getModelBundles(signal: AbortSignal): Promise<ModelBundlesSnapshot> {
    return parseModelBundlesSnapshot(await requestJson('/api/model-bundles', signal))
}

export async function startModelBundleDownload(
    bundleId: string,
    signal: AbortSignal,
): Promise<ModelBundle> {
    return parseBundle(await requestJson(
        `/api/model-bundles/${encodeURIComponent(bundleId)}/download`,
        signal,
        { method: 'POST' },
    ))
}

export async function cancelModelBundleDownload(
    bundleId: string,
    signal: AbortSignal,
): Promise<ModelBundle> {
    return parseBundle(await requestJson(
        `/api/model-bundles/${encodeURIComponent(bundleId)}/cancel`,
        signal,
        { method: 'POST' },
    ))
}

export async function deleteModelBundle(
    bundleId: string,
    signal: AbortSignal,
): Promise<BundleDeleteResult> {
    return parseDeleteResult(await requestJson(
        `/api/model-bundles/${encodeURIComponent(bundleId)}`,
        signal,
        { method: 'DELETE' },
    ))
}

export const MODEL_BUNDLE_POLL_INTERVAL_MS = 500
export const MODEL_DOWNLOAD_RATE_WINDOW_MS = 5_000

export function needsModelBundlePolling(snapshot: ModelBundlesSnapshot): boolean {
    return snapshot.activeDownloadBundleId !== null
        || snapshot.bundles.some((bundle) => bundle.phase === 'verifying')
}

export function calculateModelDownloadTelemetry(
    samples: readonly ModelDownloadProgressSample[],
    remainingBytes: number,
    nowMs: number,
): ModelDownloadTelemetry {
    const recentSamples = samples.filter((sample) => sample.timestampMs >= nowMs - MODEL_DOWNLOAD_RATE_WINDOW_MS
        && sample.timestampMs <= nowMs)
    const first = recentSamples[0]
    const last = recentSamples.at(-1)
    if (first === undefined || last === undefined) {
        return { bytesPerSecond: null, secondsRemaining: null }
    }

    const elapsedSeconds = (last.timestampMs - first.timestampMs) / 1_000
    const transferredBytes = last.transferredBytes - first.transferredBytes
    if (elapsedSeconds <= 0 || transferredBytes <= 0 || remainingBytes <= 0) {
        return { bytesPerSecond: null, secondsRemaining: null }
    }

    const bytesPerSecond = transferredBytes / elapsedSeconds
    return {
        bytesPerSecond,
        secondsRemaining: remainingBytes / bytesPerSecond,
    }
}

export function parseModelBundlesSnapshot(value: unknown): ModelBundlesSnapshot {
    if (!isRecord(value) || !Array.isArray(value.bundles)
        || !isNullableString(value.activeDownloadBundleId)
        || !isFiniteNonNegative(value.freeDiskBytes)
        || !isFiniteNonNegative(value.minimumFreeBytes)) {
        throw new Error('Backend вернул некорректное состояние комплектов моделей.')
    }
    return {
        bundles: value.bundles.map(parseBundle),
        activeDownloadBundleId: value.activeDownloadBundleId,
        freeDiskBytes: value.freeDiskBytes,
        minimumFreeBytes: value.minimumFreeBytes,
    }
}

function parseBundle(value: unknown): ModelBundle {
    if (!isRecord(value) || !Array.isArray(value.assets) || !Array.isArray(value.workflows)
        || typeof value.id !== 'string' || typeof value.title !== 'string'
        || typeof value.runtime !== 'string' || typeof value.enabled !== 'boolean'
        || typeof value.executionEnabled !== 'boolean' || typeof value.diskSufficient !== 'boolean'
        || typeof value.canDownload !== 'boolean' || typeof value.canDelete !== 'boolean'
        || !isFiniteNonNegative(value.totalBytes) || !isFiniteNonNegative(value.readyBytes)
        || !isFiniteNonNegative(value.pendingBytes) || !isPercent(value.downloadProgress)
        || !isFiniteNonNegative(value.freeDiskBytes) || !isFiniteNonNegative(value.diskRequiredBytes)
        || !isNullableString(value.blockedReason) || !isNullableString(value.executionBlockedReason)
        || !isNullableString(value.deleteBlockedReason) || !isNullableString(value.currentMessage)
        || !isNullableString(value.error)) {
        throw new Error('Backend вернул некорректное описание model bundle.')
    }
    const phase = value.phase
    if (phase !== 'waiting' && phase !== 'downloading' && phase !== 'verifying'
        && phase !== 'ready' && phase !== 'error') {
        throw new Error('Backend вернул неизвестную фазу model bundle.')
    }
    const workflows = value.workflows
    if (!workflows.every((workflow) => typeof workflow === 'string')) {
        throw new Error('Backend вернул некорректный список workflow.')
    }
    return {
        id: value.id,
        title: value.title,
        runtime: value.runtime,
        workflows: workflows as string[],
        enabled: value.enabled,
        blockedReason: value.blockedReason,
        executionEnabled: value.executionEnabled,
        executionBlockedReason: value.executionBlockedReason,
        phase,
        assets: value.assets.map(parseAsset),
        totalBytes: value.totalBytes,
        readyBytes: value.readyBytes,
        pendingBytes: value.pendingBytes,
        downloadProgress: value.downloadProgress,
        freeDiskBytes: value.freeDiskBytes,
        diskRequiredBytes: value.diskRequiredBytes,
        diskSufficient: value.diskSufficient,
        canDownload: value.canDownload,
        canDelete: value.canDelete,
        deleteBlockedReason: value.deleteBlockedReason,
        currentMessage: value.currentMessage,
        error: value.error,
    }
}

function parseAsset(value: unknown): ModelAsset {
    if (!isRecord(value) || typeof value.id !== 'string' || typeof value.repository !== 'string'
        || !isNullableString(value.revision) || typeof value.source !== 'string'
        || typeof value.target !== 'string' || !isFiniteNonNegative(value.bytes)
        || typeof value.sha256 !== 'string' || typeof value.format !== 'string'
        || typeof value.loader !== 'string' || !isPercent(value.progress)
        || !isFiniteNonNegative(value.downloadedBytes) || !isFiniteNonNegative(value.storageBytes)
        || !isNullableString(value.error)
        || typeof value.shared !== 'boolean') {
        throw new Error('Backend вернул некорректное описание model asset.')
    }
    const status = value.status
    if (status !== 'waiting' && status !== 'downloading' && status !== 'verifying'
        && status !== 'ready' && status !== 'invalid') {
        throw new Error('Backend вернул неизвестное состояние model asset.')
    }
    if (value.downloadedBytes > value.bytes) {
        throw new Error('Backend вернул лишний размер model asset.')
    }
    return {
        id: value.id,
        repository: value.repository,
        revision: value.revision,
        source: value.source,
        target: value.target,
        bytes: value.bytes,
        sha256: value.sha256,
        format: value.format,
        loader: value.loader,
        status,
        progress: value.progress,
        downloadedBytes: value.downloadedBytes,
        storageBytes: value.storageBytes,
        error: value.error,
        shared: value.shared,
    }
}

function parseDeleteResult(value: unknown): BundleDeleteResult {
    if (!isRecord(value) || typeof value.bundleId !== 'string'
        || !Array.isArray(value.deletedFiles) || !value.deletedFiles.every((path) => typeof path === 'string')
        || !Array.isArray(value.preservedSharedAssets)
        || !value.preservedSharedAssets.every((assetId) => typeof assetId === 'string')
        || !isFiniteNonNegative(value.freedBytes)) {
        throw new Error('Backend вернул некорректный результат удаления bundle.')
    }
    return {
        bundleId: value.bundleId,
        deletedFiles: value.deletedFiles as string[],
        preservedSharedAssets: value.preservedSharedAssets as string[],
        freedBytes: value.freedBytes,
    }
}

async function requestJson(path: string, signal: AbortSignal, options: RequestInit = {}): Promise<unknown> {
    const response = await fetch(path, { ...options, signal })
    const data: unknown = await response.json()
    if (!response.ok) {
        const message = isRecord(data) && typeof data.error === 'string'
            ? data.error
            : `HTTP ${response.status}`
        throw new Error(message)
    }
    return data
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isPercent(value: unknown): value is number {
    return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 100
}

function isFiniteNonNegative(value: unknown): value is number {
    return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

function isNullableString(value: unknown): value is string | null {
    return value === null || typeof value === 'string'
}
