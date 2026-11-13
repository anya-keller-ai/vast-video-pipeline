export const MAX_REFERENCE_IMAGES = 9
export const MAX_REFERENCE_AUDIOS = 3

export type OperationMode = 'generation' | 'upscale'

export interface MediaDescriptor {
    name: string
    type: string
}

export interface FileRejection {
    name: string
    reason: string
}

export interface ImageBatchValidation {
    accepted: File[]
    rejected: FileRejection[]
}

export interface MockStage {
    id: string
    label: string
    weight: number
    durationMs: number
}

export interface MockProgressEvent {
    mode: OperationMode
    stageId: string
    stageLabel: string
    stageIndex: number
    stageCount: number
    stageProgress: number
    overallProgress: number
    completedStageIds: string[]
}

export type MockProgressListener = (event: MockProgressEvent) => void
export type DemoRenderer = (reportProgress: (progress: number) => void) => Promise<Blob>
export type PipelineDelay = (durationMs: number, signal: AbortSignal) => Promise<void>

const VIDEO_EXTENSIONS = new Set(['.mp4', '.mov', '.webm', '.m4v'])
const IMAGE_EXTENSIONS = new Set(['.jpg', '.jpeg', '.png', '.webp'])
const AUDIO_EXTENSIONS = new Set(['.wav', '.mp3', '.flac', '.m4a', '.ogg', '.aac'])
const TICK_MS = 80

export const MOCK_STAGES: Record<OperationMode, readonly MockStage[]> = {
    generation: [
        { id: 'validate', label: 'Проверяю промт и референсы', weight: 12, durationMs: 480 },
        { id: 'prepare', label: 'Подготавливаю входные материалы', weight: 16, durationMs: 640 },
        { id: 'generate', label: 'Симулирую генерацию H3', weight: 42, durationMs: 1_800 },
        { id: 'render', label: 'Собираю локальный demo WebM', weight: 30, durationMs: 2_200 },
    ],
    upscale: [
        { id: 'validate', label: 'Проверяю исходное видео', weight: 14, durationMs: 480 },
        { id: 'prepare', label: 'Подготавливаю режим увеличения', weight: 18, durationMs: 640 },
        { id: 'upscale', label: 'Симулирую upscale', weight: 38, durationMs: 1_600 },
        { id: 'render', label: 'Собираю локальный demo WebM', weight: 30, durationMs: 2_200 },
    ],
}

export function validateVideoFile(file: MediaDescriptor): string | null {
    const extension = getExtension(file.name)
    const hasSupportedExtension = VIDEO_EXTENSIONS.has(extension)
    const hasSupportedMime = file.type === ''
        || file.type === 'application/octet-stream'
        || file.type.startsWith('video/')

    if (hasSupportedExtension && hasSupportedMime) {
        return null
    }

    return 'Поддерживаются видео MP4, WebM и MOV. Выбери видеофайл с одним из этих расширений.'
}

export function validateAudioBatch(existingCount: number, candidates: readonly File[], maxAudios: number = MAX_REFERENCE_AUDIOS): ImageBatchValidation {
    const accepted: File[] = []
    const rejected: FileRejection[] = []
    let audioCount = Math.min(Math.max(existingCount, 0), maxAudios)

    for (const file of candidates) {
        const hasSupportedMime = file.type === '' || file.type === 'application/octet-stream' || file.type.startsWith('audio/')
        if (!AUDIO_EXTENSIONS.has(getExtension(file.name)) || !hasSupportedMime) {
            rejected.push({ name: file.name, reason: 'Поддерживаются аудио WAV, MP3, FLAC, M4A, OGG и AAC.' })
            continue
        }

        if (audioCount >= maxAudios) {
            rejected.push({ name: file.name, reason: `Можно добавить не больше ${maxAudios} аудио.` })
            continue
        }

        accepted.push(file)
        audioCount += 1
    }

    return { accepted, rejected }
}

export function validateImageBatch(existingCount: number, candidates: readonly File[], maxImages: number = MAX_REFERENCE_IMAGES): ImageBatchValidation {
    const accepted: File[] = []
    const rejected: FileRejection[] = []
    let imageCount = Math.min(Math.max(existingCount, 0), maxImages)

    for (const file of candidates) {
        if (!isSupportedImage(file)) {
            rejected.push({ name: file.name, reason: 'Поддерживаются изображения JPG, PNG и WebP.' })
            continue
        }

        if (imageCount >= maxImages) {
            rejected.push({ name: file.name, reason: `Можно добавить не больше ${maxImages} изображений.` })
            continue
        }

        accepted.push(file)
        imageCount += 1
    }

    return { accepted, rejected }
}

export function isPromptValid(prompt: string): boolean {
    return prompt.trim().length > 0
}

export async function runMockPipeline(
    mode: OperationMode,
    onProgress: MockProgressListener,
    renderResult: DemoRenderer,
    signal: AbortSignal,
    wait: PipelineDelay = delay,
): Promise<Blob> {
    const stages = MOCK_STAGES[mode]
    const totalWeight = stages.reduce((total, stage) => total + stage.weight, 0)
    const completedStageIds: string[] = []
    let completedWeight = 0
    let result: Blob | null = null

    for (const [stageIndex, stage] of stages.entries()) {
        throwIfAborted(signal)
        emitProgress(onProgress, mode, stages, stageIndex, 0, completedWeight, totalWeight, completedStageIds)

        if (stage.id === 'render') {
            result = await renderResult((stageProgress) => {
                const boundedProgress = clampProgress(stageProgress)
                emitProgress(onProgress, mode, stages, stageIndex, boundedProgress, completedWeight, totalWeight, completedStageIds)
            })
        } else {
            let elapsedMs = 0

            while (elapsedMs < stage.durationMs) {
                throwIfAborted(signal)
                const stageProgress = Math.min(elapsedMs / stage.durationMs, 1)
                emitProgress(onProgress, mode, stages, stageIndex, stageProgress, completedWeight, totalWeight, completedStageIds)
                const tickMs = Math.min(TICK_MS, stage.durationMs - elapsedMs)
                await wait(tickMs, signal)
                elapsedMs += tickMs
            }
        }

        throwIfAborted(signal)
        completedStageIds.push(stage.id)
        completedWeight += stage.weight
        emitProgress(onProgress, mode, stages, stageIndex, 1, completedWeight - stage.weight, totalWeight, completedStageIds)
    }

    if (result === null) {
        throw new Error('Mock pipeline завершился без demo-видео.')
    }

    return result
}

function isSupportedImage(file: MediaDescriptor): boolean {
    const extension = getExtension(file.name)
    const hasSupportedExtension = IMAGE_EXTENSIONS.has(extension)
    const hasSupportedMime = file.type === ''
        || file.type === 'application/octet-stream'
        || file.type.startsWith('image/')

    return hasSupportedExtension && hasSupportedMime
}

function getExtension(filename: string): string {
    const lastDot = filename.lastIndexOf('.')
    return lastDot < 0 ? '' : filename.slice(lastDot).toLowerCase()
}

function emitProgress(
    listener: MockProgressListener,
    mode: OperationMode,
    stages: readonly MockStage[],
    stageIndex: number,
    stageProgress: number,
    completedWeight: number,
    totalWeight: number,
    completedStageIds: readonly string[],
): void {
    const stage = stages[stageIndex]

    if (stage === undefined) {
        return
    }

    const overallProgress = (completedWeight + stage.weight * stageProgress) / totalWeight

    listener({
        mode,
        stageId: stage.id,
        stageLabel: stage.label,
        stageIndex,
        stageCount: stages.length,
        stageProgress: Math.round(stageProgress * 100),
        overallProgress: Math.round(overallProgress * 100),
        completedStageIds: [...completedStageIds],
    })
}

function clampProgress(progress: number): number {
    if (!Number.isFinite(progress)) {
        return 0
    }

    return Math.min(Math.max(progress, 0), 1)
}

function throwIfAborted(signal: AbortSignal): void {
    if (signal.aborted) {
        const error = new Error('Mock-задача остановлена')
        error.name = 'AbortError'
        throw error
    }
}

function delay(durationMs: number, signal: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
        if (signal.aborted) {
            const error = new Error('Mock-задача остановлена')
            error.name = 'AbortError'
            reject(error)
            return
        }

        const timeout = globalThis.setTimeout(() => {
            signal.removeEventListener('abort', abort)
            resolve()
        }, durationMs)

        const abort = () => {
            globalThis.clearTimeout(timeout)
            signal.removeEventListener('abort', abort)
            const error = new Error('Mock-задача остановлена')
            error.name = 'AbortError'
            reject(error)
        }

        signal.addEventListener('abort', abort, { once: true })
    })
}
