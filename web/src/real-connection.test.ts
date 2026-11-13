import { afterEach, describe, expect, it, vi } from 'vitest'
import { createInitialReadiness, getDownloadedBytes } from './model-readiness'
import { getRuntimeConfig, parseJob, parseReadiness, runRealJob, runRealReadiness } from './real-connection'

function readySnapshot() {
    const snapshot = createInitialReadiness()
    return {
        ...snapshot, phase: 'ready', comfyUi: 'ready',
        models: snapshot.models.map((model) => ({ ...model, status: 'ready', progress: 100, downloadedBytes: model.bytes })),
    }
}

afterEach(() => vi.unstubAllGlobals())

describe('real connection boundary', () => {
    it('accepts checked readiness and exact server byte counts', () => {
        const snapshot = parseReadiness(readySnapshot())
        expect(snapshot.phase).toBe('ready')
        expect(getDownloadedBytes(snapshot.models)).toBe(snapshot.models.reduce((sum, model) => sum + model.bytes, 0))
    })

    it('rejects stale or malformed model manifests', () => {
        const snapshot = readySnapshot()
        expect(() => parseReadiness({ ...snapshot, models: [] })).toThrow('Манифест')
        expect(() => parseReadiness({ ...snapshot, phase: 'unknown' })).toThrow('фаза')
        expect(() => parseReadiness({ ...snapshot, models: snapshot.models.map((model) => ({ ...model, downloadedBytes: -1 })) })).toThrow('байтов')
    })

    it('does not invent an overall percentage for a node without measured progress', () => {
        const job = parseJob({
            id: 'job', status: 'running', currentMessage: 'Loading a model', filename: 'video.mp4',
            progressPercent: null, progressLabel: null, resultUrl: null, error: null,
        })
        expect(job.progressPercent).toBeNull()
    })

    it('parses cancelled status and server elapsed time', () => {
        const job = parseJob({
            id: 'job', status: 'cancelled', currentMessage: 'Отменено', filename: 'video.mp4',
            progressPercent: null, progressLabel: null, resultUrl: null, error: null, elapsedSeconds: 12.5,
        })
        expect(job.status).toBe('cancelled')
        expect(job.elapsedSeconds).toBe(12.5)
    })

    it('rejects external output URLs', () => {
        expect(() => parseJob({
            id: 'job', status: 'complete', currentMessage: 'Done', filename: 'video.mp4',
            progressPercent: 100, progressLabel: null, resultUrl: 'https://untrusted.example/video.mp4', error: null,
        })).toThrow('путь')
    })

    it('loads REAL_CONNECTION only from the backend response', async () => {
        const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ realConnection: true })))
        vi.stubGlobal('fetch', fetchMock)
        await expect(getRuntimeConfig(new AbortController().signal)).resolves.toEqual({ realConnection: true })
        expect(fetchMock).toHaveBeenCalledWith('/api/config', expect.objectContaining({ signal: expect.any(AbortSignal) }))
    })

    it('cannot fall back silently to mock after backend failure', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: 'Disconnected' }), { status: 502 })))
        await expect(getRuntimeConfig(new AbortController().signal)).rejects.toThrow('Disconnected')
    })

    it('rejects Pika submission without any reference before uploading files', async () => {
        const fetchMock = vi.fn()
        vi.stubGlobal('fetch', fetchMock)
        await expect(runRealJob({
            mode: 'pika', prompt: 'scene', images: [], video: null, durationSeconds: 5, resolution: 'standard', seed: 42, refImageSize: 'match',
        }, vi.fn(), new AbortController().signal)).rejects.toThrow('изображение или видео-референс')
        expect(fetchMock).not.toHaveBeenCalled()
    })

    it('uses checked readiness from a real-mode API without timers or simulated models', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(readySnapshot()))))
        const update = vi.fn()
        await runRealReadiness(update, new AbortController().signal)
        expect(update).toHaveBeenCalledOnce()
        expect(update).toHaveBeenCalledWith(expect.objectContaining({ phase: 'ready' }))
    })
})
