import modelManifest from '../../config/models.lock.json'

export type ModelStatus = 'waiting' | 'downloading' | 'verifying' | 'ready'
export type ComfyUiStatus = 'waiting' | 'starting' | 'ready'
export type ReadinessPhase = 'starting' | 'downloading' | 'verifying' | 'starting-comfyui' | 'ready' | 'error'

export interface ModelFile {
    id: string
    repository: string
    target: string
    bytes: number
    sha256: string
    required: boolean
}

export interface ModelProgress extends ModelFile {
    status: ModelStatus
    progress: number
    downloadedBytes?: number
}

export interface ReadinessSnapshot {
    phase: ReadinessPhase
    models: ModelProgress[]
    comfyUi: ComfyUiStatus
    currentMessage: string
}

export type ReadinessListener = (snapshot: ReadinessSnapshot) => void
export type ReadinessDelay = (durationMs: number, signal: AbortSignal) => Promise<void>

const REQUIRED_MODELS = (modelManifest.models as ModelFile[]).filter((model) => model.required)
const DOWNLOAD_STEPS = [14, 32, 52, 72, 91, 100] as const
const DOWNLOAD_TICK_MS = 130
const VERIFY_DELAY_MS = 220

export const REQUIRED_MODEL_FILES: readonly ModelFile[] = REQUIRED_MODELS
export const REQUIRED_MODELS_BYTES = REQUIRED_MODELS.reduce((total, model) => total + model.bytes, 0)

export function createInitialReadiness(): ReadinessSnapshot {
    return {
        phase: 'starting',
        models: REQUIRED_MODELS.map((model) => ({
            ...model,
            status: 'waiting',
            progress: 0,
        })),
        comfyUi: 'waiting',
        currentMessage: 'Подключаюсь к локальному mock-инстансу',
    }
}

export function getModelFilename(model: ModelFile): string {
    const pathParts = model.target.split('/')
    return pathParts[pathParts.length - 1] ?? model.target
}

export function getDownloadedBytes(models: readonly ModelProgress[]): number {
    return models.reduce((total, model) => total + (model.downloadedBytes ?? model.bytes * model.progress / 100), 0)
}

export function formatBytes(bytes: number): string {
    const gibibyte = 1024 ** 3
    const mebibyte = 1024 ** 2

    if (bytes >= gibibyte) {
        return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(bytes / gibibyte)} ГиБ`
    }

    if (bytes >= mebibyte) {
        return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(bytes / mebibyte)} МиБ`
    }

    return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(bytes / 1024)} КиБ`
}

export async function runModelReadinessMock(
    onUpdate: ReadinessListener,
    signal: AbortSignal,
    wait: ReadinessDelay = delay,
): Promise<void> {
    let snapshot = createInitialReadiness()
    onUpdate(snapshot)
    await wait(500, signal)

    snapshot = {
        ...snapshot,
        phase: 'downloading',
        currentMessage: 'Загружаю файлы моделей последовательно',
    }
    onUpdate(snapshot)

    for (const [index, model] of REQUIRED_MODELS.entries()) {
        snapshot = updateModel(snapshot, index, 'downloading', 0, `Загружается ${getModelFilename(model)}`)
        onUpdate(snapshot)

        for (const progress of DOWNLOAD_STEPS) {
            await wait(DOWNLOAD_TICK_MS, signal)
            snapshot = updateModel(snapshot, index, 'downloading', progress, `Загружается ${getModelFilename(model)}`)
            onUpdate(snapshot)
        }

        snapshot = updateModel(snapshot, index, 'verifying', 100, `Проверяю размер и SHA-256: ${getModelFilename(model)}`)
        onUpdate(snapshot)
        await wait(VERIFY_DELAY_MS, signal)
        snapshot = updateModel(snapshot, index, 'ready', 100, `Проверено: ${getModelFilename(model)}`)
        onUpdate(snapshot)
    }

    snapshot = {
        ...snapshot,
        phase: 'starting-comfyui',
        comfyUi: 'starting',
        currentMessage: 'Проверяю готовность ComfyUI',
    }
    onUpdate(snapshot)
    await wait(900, signal)

    snapshot = {
        ...snapshot,
        phase: 'ready',
        comfyUi: 'ready',
        currentMessage: 'Модели проверены, ComfyUI готов',
    }
    onUpdate(snapshot)
}

function updateModel(
    snapshot: ReadinessSnapshot,
    modelIndex: number,
    status: ModelStatus,
    progress: number,
    currentMessage: string,
): ReadinessSnapshot {
    return {
        ...snapshot,
        phase: status === 'verifying' ? 'verifying' : 'downloading',
        currentMessage,
        models: snapshot.models.map((model, index) => index === modelIndex
            ? { ...model, status, progress }
            : model),
    }
}

function delay(durationMs: number, signal: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
        if (signal.aborted) {
            reject(createAbortError())
            return
        }

        const timeout = globalThis.setTimeout(() => {
            signal.removeEventListener('abort', abort)
            resolve()
        }, durationMs)

        const abort = () => {
            globalThis.clearTimeout(timeout)
            signal.removeEventListener('abort', abort)
            reject(createAbortError())
        }

        signal.addEventListener('abort', abort, { once: true })
    })
}

function createAbortError(): Error {
    const error = new Error('Подготовка mock-инстанса остановлена')
    error.name = 'AbortError'
    return error
}
