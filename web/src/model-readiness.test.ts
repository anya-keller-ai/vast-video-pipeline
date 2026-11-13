import { describe, expect, it } from 'vitest'
import {
    createInitialReadiness,
    formatBytes,
    getDownloadedBytes,
    REQUIRED_MODEL_FILES,
    REQUIRED_MODELS_BYTES,
    runModelReadinessMock,
} from './model-readiness'
import type { ReadinessDelay, ReadinessSnapshot } from './model-readiness'

describe('model readiness mock', () => {
    it('reads only required model files from the project manifest', () => {
        const snapshot = createInitialReadiness()

        expect(REQUIRED_MODEL_FILES.length).toBeGreaterThan(0)
        expect(snapshot.models).toHaveLength(REQUIRED_MODEL_FILES.length)
        expect(snapshot.models.every((model) => model.required)).toBe(true)
        expect(REQUIRED_MODELS_BYTES).toBe(snapshot.models.reduce((total, model) => total + model.bytes, 0))
        expect(getDownloadedBytes(snapshot.models)).toBe(0)
    })

    it('simulates download, verification and ComfyUI readiness in order', async () => {
        const snapshots: ReadinessSnapshot[] = []
        const wait: ReadinessDelay = async () => undefined
        const controller = new AbortController()

        await runModelReadinessMock((snapshot) => snapshots.push(snapshot), controller.signal, wait)

        const finalSnapshot = snapshots[snapshots.length - 1]
        expect(finalSnapshot?.phase).toBe('ready')
        expect(finalSnapshot?.comfyUi).toBe('ready')
        expect(finalSnapshot?.models.every((model) => model.status === 'ready' && model.progress === 100)).toBe(true)
        expect(getDownloadedBytes(finalSnapshot?.models ?? [])).toBe(REQUIRED_MODELS_BYTES)
        expect(snapshots.some((snapshot) => snapshot.phase === 'verifying')).toBe(true)
        expect(snapshots.some((snapshot) => snapshot.comfyUi === 'starting')).toBe(true)
    })

    it('stops promptly when the mock startup is aborted', async () => {
        const controller = new AbortController()
        controller.abort()

        await expect(runModelReadinessMock(() => undefined, controller.signal)).rejects.toMatchObject({
            name: 'AbortError',
        })
    })

    it('formats file sizes for a Russian-language interface', () => {
        expect(formatBytes(1024)).toBe('1 КиБ')
        expect(formatBytes(1024 ** 2)).toBe('1 МиБ')
        expect(formatBytes(1024 ** 3)).toBe('1 ГиБ')
    })
})
