import { describe, expect, it } from 'vitest'
import {
    calculateModelDownloadTelemetry,
    MODEL_BUNDLE_POLL_INTERVAL_MS,
    needsModelBundlePolling,
    parseModelBundlesSnapshot,
} from './model-bundles'

const asset = {
    id: 'asset-a',
    repository: 'org/repo',
    revision: 'abc123',
    source: 'weights/a.safetensors',
    target: 'checkpoints/a.safetensors',
    bytes: 16,
    sha256: 'a'.repeat(64),
    format: 'safetensors-bf16',
    loader: 'comfyui-checkpoint',
    status: 'waiting',
    progress: 0,
    downloadedBytes: 0,
    storageBytes: 0,
    error: null,
    shared: false,
}

function snapshot(activeDownloadBundleId: string | null, phase = 'waiting') {
    return {
        bundles: [{
            id: 'h3_base',
            title: 'MiniMax H3 · Base',
            runtime: 'comfyui',
            workflows: ['h3_ref2v_base'],
            enabled: true,
            blockedReason: null,
            executionEnabled: true,
            executionBlockedReason: null,
            phase,
            assets: [asset],
            totalBytes: 16,
            readyBytes: 0,
            pendingBytes: 16,
            downloadProgress: 0,
            freeDiskBytes: 1_000,
            diskRequiredBytes: 17,
            diskSufficient: true,
            canDownload: true,
            canDelete: true,
            deleteBlockedReason: null,
            currentMessage: null,
            error: null,
        }],
        activeDownloadBundleId,
        freeDiskBytes: 1_000,
        minimumFreeBytes: 1,
    }
}

describe('model download telemetry', () => {
    it('polls twice per second', () => {
        expect(MODEL_BUNDLE_POLL_INTERVAL_MS).toBe(500)
    })

    it('calculates recent transfer speed and estimated remaining time', () => {
        const mebibyte = 1024 ** 2
        const telemetry = calculateModelDownloadTelemetry([
            { timestampMs: 0, transferredBytes: 0 },
            { timestampMs: 2_500, transferredBytes: 5 * mebibyte },
            { timestampMs: 5_000, transferredBytes: 10 * mebibyte },
        ], 20 * mebibyte, 5_000)

        expect(telemetry.bytesPerSecond).toBe(2 * mebibyte)
        expect(telemetry.secondsRemaining).toBe(10)
    })

    it('does not estimate until there are two recent increasing samples', () => {
        expect(calculateModelDownloadTelemetry([
            { timestampMs: 1_000, transferredBytes: 1_000 },
        ], 9_000, 1_000)).toEqual({ bytesPerSecond: null, secondsRemaining: null })
        expect(calculateModelDownloadTelemetry([
            { timestampMs: 1_000, transferredBytes: 1_000 },
            { timestampMs: 2_000, transferredBytes: 1_000 },
        ], 9_000, 2_000)).toEqual({ bytesPerSecond: null, secondsRemaining: null })
        expect(calculateModelDownloadTelemetry([
            { timestampMs: 1_000, transferredBytes: 2_000 },
            { timestampMs: 2_000, transferredBytes: 1_000 },
        ], 9_000, 2_000)).toEqual({ bytesPerSecond: null, secondsRemaining: null })
    })

    it('ignores stale progress samples', () => {
        expect(calculateModelDownloadTelemetry([
            { timestampMs: 0, transferredBytes: 1_000 },
            { timestampMs: 500, transferredBytes: 2_000 },
        ], 9_000, 6_000)).toEqual({ bytesPerSecond: null, secondsRemaining: null })
    })
})

describe('model bundle API response parsing', () => {
    it('parses independent per-bundle model status without implying a download', () => {
        const parsed = parseModelBundlesSnapshot(snapshot(null))

        expect(parsed.activeDownloadBundleId).toBeNull()
        expect(parsed.bundles[0]?.assets[0]?.status).toBe('waiting')
        expect(needsModelBundlePolling(parsed)).toBe(false)
    })

    it('polls only while a transfer or checksum verification is active', () => {
        expect(needsModelBundlePolling(parseModelBundlesSnapshot(snapshot('h3_base')))).toBe(true)
        expect(needsModelBundlePolling(parseModelBundlesSnapshot(snapshot(null, 'verifying')))).toBe(true)
    })

    it('rejects unknown model asset states', () => {
        const malformed = snapshot(null)
        malformed.bundles[0]!.assets[0]!.status = 'unknown'

        expect(() => parseModelBundlesSnapshot(malformed)).toThrow('Backend вернул неизвестное состояние model asset')
    })
})
