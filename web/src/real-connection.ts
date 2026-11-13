import type { H3Resolution } from './h3-generation'
import { REQUIRED_MODEL_FILES } from './model-readiness'
import type { ReadinessListener, ReadinessSnapshot } from './model-readiness'

export interface RuntimeConfig {
    realConnection: boolean
}

export type SeedVrModel = '3b' | '7b' | '7b_sharp'

export interface RealJobProgress {
    id: string | null
    status: 'uploading' | 'queued' | 'running' | 'complete' | 'error' | 'cancelled'
    currentMessage: string
    progressPercent: number | null
    progressLabel: string | null
    resultUrl: string | null
    filename: string
    error: string | null
    elapsedSeconds: number | null
}


export type FlashVsrVariant = 'turbo' | 'base'
export type InterpolationModel = 'film' | 'rife_4_26' | 'rife_4_25'
export type UpscaleModelKey = 'realesrgan_x2plus' | 'realesrgan_animevideo_v3'

interface RealJobMediaOptions {
    prompt: string
    images: readonly File[]
    video: File | null
    audios?: readonly File[]
}

export type RealJobOptions = (RealJobMediaOptions & {
    mode: 'generation'
    durationSeconds: number
    resolution: H3Resolution
    turboLora: boolean
}) | (RealJobMediaOptions & {
    mode: 'upscale'
    scale: 2 | 4
    model: SeedVrModel
    batchSize: number
    temporalOverlap: number
}) | (RealJobMediaOptions & {
    mode: 'interpolate'
    model: InterpolationModel
    multiplier: 2 | 3
}) | (RealJobMediaOptions & {
    mode: 'upscale_model'
    model: UpscaleModelKey
}) | (RealJobMediaOptions & {
    mode: 'flashvsr'
    scale: 2 | 4
    variant: FlashVsrVariant
}) | (RealJobMediaOptions & {
    mode: 'pika'
    durationSeconds: number
    resolution: H3Resolution
    seed: number
    refImageSize: 'match' | 'max'
})

export async function getRuntimeConfig(signal: AbortSignal): Promise<RuntimeConfig> {
    const data = await requestJson('/api/config', signal)
    if (!isRecord(data) || typeof data.realConnection !== 'boolean') {
        throw new Error('Backend не вернул корректный REAL_CONNECTION.')
    }
    return { realConnection: data.realConnection }
}

export async function runRealReadiness(onUpdate: ReadinessListener, signal: AbortSignal): Promise<void> {
    let snapshot = parseReadiness(await requestJson('/api/readiness/start', signal, { method: 'POST' }))
    onUpdate(snapshot)
    while (snapshot.phase !== 'ready') {
        if (snapshot.phase === 'error') {
            throw new Error(snapshot.currentMessage)
        }
        await waitForPoll(signal)
        snapshot = parseReadiness(await requestJson('/api/readiness', signal))
        onUpdate(snapshot)
    }
}

export async function cancelRealReadiness(): Promise<void> {
    await requestJson('/api/readiness/cancel', new AbortController().signal, { method: 'POST' })
}

export async function runRealJob(
    options: RealJobOptions,
    onUpdate: (progress: RealJobProgress) => void,
    signal: AbortSignal,
): Promise<RealJobProgress> {
    const uploadedIds: string[] = []
    let submitted = false
    try {
        if (options.mode === 'pika' && options.images.length === 0 && options.video === null) {
            throw new Error('Pika Ref2VA требует изображение или видео-референс.')
        }
        const imageIds: string[] = []
        for (const image of options.images) {
            const id = await uploadFile(image, onUpdate, signal)
            imageIds.push(id)
            uploadedIds.push(id)
        }
        const audioIds: string[] = []
        for (const audio of options.audios ?? []) {
            const id = await uploadFile(audio, onUpdate, signal)
            audioIds.push(id)
            uploadedIds.push(id)
        }
        const videoId = options.video === null ? null : await uploadFile(options.video, onUpdate, signal)
        if (videoId !== null) {
            uploadedIds.push(videoId)
        }
        const body = options.mode === 'generation'
            ? {
                mode: options.mode,
                prompt: options.prompt,
                referenceImageIds: imageIds,
                referenceVideoId: videoId,
                durationSeconds: options.durationSeconds,
                resolution: options.resolution,
                turboLora: options.turboLora,
                referenceAudioIds: audioIds,
            }
            : options.mode === 'interpolate'
                ? { mode: options.mode, videoId, model: options.model, multiplier: options.multiplier }
                : options.mode === 'upscale_model'
                    ? { mode: options.mode, videoId, model: options.model }
                    : options.mode === 'flashvsr'
                        ? { mode: options.mode, videoId, scale: options.scale, variant: options.variant }
                        : options.mode === 'pika'
                            ? {
                                mode: options.mode,
                                prompt: options.prompt,
                                referenceImageIds: imageIds,
                                referenceVideoId: videoId,
                                durationSeconds: options.durationSeconds,
                                resolution: options.resolution,
                                seed: options.seed,
                                refImageSize: options.refImageSize,
                                referenceAudioIds: audioIds,
                            }
                            : { mode: options.mode, videoId, scale: options.scale, model: options.model, batchSize: options.batchSize, temporalOverlap: options.temporalOverlap }
        let job = parseJob(await requestJson('/api/jobs', signal, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
        }))
        submitted = true
        onUpdate(job)
        while (job.status !== 'complete') {
            if (job.status === 'cancelled') {
                return job
            }
            if (job.status === 'error') {
                throw new Error(job.error ?? job.currentMessage)
            }
            if (job.id === null) {
                throw new Error('Backend не вернул идентификатор ComfyUI job.')
            }
            await waitForPoll(signal)
            job = parseJob(await requestJson(`/api/jobs/${encodeURIComponent(job.id)}`, signal))
            onUpdate(job)
        }
        if (job.resultUrl === null) {
            throw new Error('ComfyUI job завершился без видеофайла.')
        }
        return job
    } finally {
        if (!submitted) {
            await Promise.all(uploadedIds.map(async (id) => {
                await fetch(`/api/uploads/${encodeURIComponent(id)}`, { method: 'DELETE' }).catch(() => undefined)
            }))
        }
    }
}

export async function cancelRealJob(jobId: string): Promise<RealJobProgress> {
    const data = await requestJson(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, new AbortController().signal, { method: 'POST' })
    return parseJob(data)
}

export function parseReadiness(value: unknown): ReadinessSnapshot {
    if (!isRecord(value) || !Array.isArray(value.models) || typeof value.currentMessage !== 'string') {
        throw new Error('Некорректный ответ готовности Vast.')
    }
    const phase = value.phase
    const comfyUi = value.comfyUi
    if (phase !== 'starting' && phase !== 'downloading' && phase !== 'verifying'
        && phase !== 'starting-comfyui' && phase !== 'ready' && phase !== 'error') {
        throw new Error('Неизвестная фаза готовности Vast.')
    }
    if (comfyUi !== 'waiting' && comfyUi !== 'starting' && comfyUi !== 'ready') {
        throw new Error('Неизвестный статус ComfyUI.')
    }
    const models: unknown[] = value.models
    if (models.length !== REQUIRED_MODEL_FILES.length) {
        throw new Error('Манифест браузера не совпадает с backend; обнови страницу.')
    }
    return {
        phase, comfyUi, currentMessage: value.currentMessage,
        models: REQUIRED_MODEL_FILES.map((model) => {
            const raw = models.find((candidate) => isRecord(candidate) && candidate.id === model.id)
            if (!isRecord(raw) || raw.bytes !== model.bytes || raw.sha256 !== model.sha256
                || raw.target !== model.target || !isPercent(raw.progress)) {
                throw new Error('Манифест браузера не совпадает с backend; обнови страницу.')
            }
            const status = raw.status
            if (status !== 'waiting' && status !== 'downloading' && status !== 'verifying' && status !== 'ready') {
                throw new Error('Неизвестный статус model-файла.')
            }
            const downloadedBytes = raw.downloadedBytes
            if (typeof downloadedBytes !== 'number' || !Number.isFinite(downloadedBytes)
                || downloadedBytes < 0 || downloadedBytes > model.bytes) {
                throw new Error('Backend вернул некорректное число загруженных байтов.')
            }
            return { ...model, status, progress: raw.progress, downloadedBytes }
        }),
    }
}

export function parseJob(value: unknown): RealJobProgress {
    if (!isRecord(value) || typeof value.id !== 'string' || typeof value.currentMessage !== 'string'
        || typeof value.filename !== 'string') {
        throw new Error('Backend вернул некорректную ComfyUI job.')
    }
    const status = value.status
    if (status !== 'queued' && status !== 'running' && status !== 'complete' && status !== 'error' && status !== 'cancelled') {
        throw new Error('Неизвестный статус ComfyUI job.')
    }
    if (value.progressPercent !== null && !isPercent(value.progressPercent)) {
        throw new Error('Backend вернул некорректный прогресс узла.')
    }
    const resultUrl = value.resultUrl
    if (resultUrl !== null && (typeof resultUrl !== 'string' || !resultUrl.startsWith('/api/jobs/'))) {
        throw new Error('Некорректный путь результата ComfyUI.')
    }
    const progressLabel = typeof value.progressLabel === 'string' ? value.progressLabel : null
    const error = typeof value.error === 'string' ? value.error : null
    const elapsedSeconds = typeof value.elapsedSeconds === 'number' && Number.isFinite(value.elapsedSeconds) ? value.elapsedSeconds : null
    return {
        id: value.id, status, currentMessage: value.currentMessage,
        progressPercent: value.progressPercent, progressLabel, resultUrl, filename: value.filename, error, elapsedSeconds,
    }
}

async function requestJson(path: string, signal: AbortSignal, options: RequestInit = {}): Promise<unknown> {
    const response = await fetch(path, { ...options, signal })
    const data: unknown = await response.json()
    if (!response.ok) {
        const message = isRecord(data) && typeof data.error === 'string' ? data.error : `HTTP ${response.status}`
        throw new Error(message)
    }
    return data
}

function uploadFile(file: File, onUpdate: (progress: RealJobProgress) => void, signal: AbortSignal): Promise<string> {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest()
        const uploadId = crypto.randomUUID()
        const report = (percent: number | null) => onUpdate({
            id: null, status: 'uploading', currentMessage: `Передаю на Vast: ${file.name}`,
            progressPercent: percent, progressLabel: 'Передача текущего файла',
            resultUrl: null, filename: '', error: null, elapsedSeconds: null,
        })
        const abort = () => xhr.abort()
        const cleanup = () => signal.removeEventListener('abort', abort)
        xhr.open('PUT', `/api/uploads/${uploadId}`)
        xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name))
        xhr.setRequestHeader('Content-Type', 'application/octet-stream')
        xhr.upload.onprogress = (event) => report(event.lengthComputable ? Math.round(event.loaded / event.total * 100) : null)
        xhr.onerror = () => {
            cleanup()
            reject(new Error(`Не удалось передать ${file.name} на Vast.`))
        }
        xhr.onabort = () => {
            cleanup()
            reject(new DOMException('Передача остановлена', 'AbortError'))
        }
        xhr.onload = () => {
            cleanup()
            try {
                const data: unknown = JSON.parse(xhr.responseText)
                if (!isRecord(data) || xhr.status >= 400 || typeof data.id !== 'string') {
                    const message = isRecord(data) && typeof data.error === 'string' ? data.error : 'Некорректный ответ загрузки файла.'
                    throw new Error(message)
                }
                resolve(data.id)
            } catch (error: unknown) {
                reject(error)
            }
        }
        if (signal.aborted) {
            reject(new DOMException('Передача остановлена', 'AbortError'))
            return
        }
        signal.addEventListener('abort', abort, { once: true })
        report(0)
        xhr.send(file)
    })
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isPercent(value: unknown): value is number {
    return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100
}

function waitForPoll(signal: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
        if (signal.aborted) {
            reject(new DOMException('Опрос остановлен', 'AbortError'))
            return
        }
        const abort = () => {
            clearTimeout(timer)
            reject(new DOMException('Опрос остановлен', 'AbortError'))
        }
        const timer = setTimeout(() => {
            signal.removeEventListener('abort', abort)
            resolve()
        }, 1_000)
        signal.addEventListener('abort', abort, { once: true })
    })
}
