import { For, Show, createEffect, createSignal, onCleanup, onMount, untrack } from 'solid-js'
import type { JSX, Setter } from 'solid-js'
import { tv } from 'tailwind-variants'
import { formatBytes } from './model-readiness'
import {
    calculateModelDownloadTelemetry,
    cancelModelBundleDownload,
    deleteModelBundle,
    getModelBundles,
    MODEL_BUNDLE_POLL_INTERVAL_MS,
    MODEL_DOWNLOAD_RATE_WINDOW_MS,
    needsModelBundlePolling,
    startModelBundleDownload,
} from './model-bundles'
import type {
    ModelBundle,
    ModelBundlesSnapshot,
    ModelDownloadProgressSample,
    ModelDownloadTelemetry,
} from './model-bundles'
import {
    isPromptValid,
    MAX_REFERENCE_AUDIOS,
    MAX_REFERENCE_IMAGES,
    MOCK_STAGES,
    runMockPipeline,
    validateAudioBatch,
    validateImageBatch,
    validateVideoFile,
} from './mock-pipeline'
import type { MockProgressEvent, OperationMode } from './mock-pipeline'
import {
    formatH3Duration,
    formatH3Resolution,
    H3_DURATION_OPTIONS,
    H3_RESOLUTION_OPTIONS,
    H3_OUTPUT_HEIGHT,
    H3_FRAMES_PER_SECOND,
    H3_OUTPUT_WIDTH,
    h3GeneratedDurationSeconds,
    h3FrameCountForDuration,
    isNegativeInputValid,
    NEGATIVE_DEFAULT_STRENGTH,
    NEGATIVE_MAX_CHARS,
    NEGATIVE_MAX_STRENGTH,
    NEGATIVE_MIN_STRENGTH,
    negativeInputError,
    negativePromptError,
} from './h3-generation'
import type { H3Resolution } from './h3-generation'
import { createDemoWebM } from './mock-video'
import type { DemoVideoOptions } from './mock-video'
import { cancelRealJob, getRuntimeConfig, runRealJob } from './real-connection'
import type { FlashVsrVariant, InterpolationModel, RealJobOptions, UpscaleModelKey, RealJobProgress, SeedVrModel } from './real-connection'

/* Hallmark · audience: video creator · use: generate/upscale locally · tone: focused workshop
 * genre: atmospheric · macrostructure: Workbench · theme: Bloom · enrichment: none
 * nav: N9 edge-aligned minimal · footer: Ft2 inline rule · previous: none
 */

const buttonStyles = tv({
    base: 'inline-flex min-h-11 items-center justify-center gap-2 whitespace-nowrap rounded-md px-4 font-body text-sm font-semibold transition-colors duration-150 [transition-timing-function:var(--ease-out)] active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus',
    variants: {
        tone: {
            primary: 'bg-accent text-accent-ink hover:bg-accent-strong',
            secondary: 'border border-rule bg-panel text-ink hover:bg-paper-2',
            quiet: 'text-muted hover:bg-paper-2 hover:text-ink',
        },
        size: {
            regular: 'px-4',
            small: 'min-h-10 px-3 text-xs',
        },
    },
    defaultVariants: {
        tone: 'secondary',
        size: 'regular',
    },
})

const statusStyles = tv({
    base: 'inline-flex items-center gap-2 rounded-full border px-2.5 py-1 font-mono text-[0.68rem] leading-none',
    variants: {
        tone: {
            ready: 'border-good-rule bg-good-wash text-good-ink',
            active: 'border-accent/40 bg-accent-wash text-accent-strong',
            waiting: 'border-rule bg-paper-2 text-muted',
            error: 'border-error-rule bg-error-wash text-error-ink',
        },
    },
})

type ModelFamily = 'h3' | 'h3-turbo' | 'pika' | 'seedvr2' | 'flashvsr-turbo' | 'flashvsr-base' | 'realesrgan-x2plus' | 'realesrgan-animevideo' | 'interpolation'
type GenerationVariant = 'h3' | 'h3-turbo' | 'pika'

type WorkspaceTab = 'generation' | 'upscale'
type JobStatus = 'idle' | 'running' | 'complete' | 'error' | 'cancelled'
type JobSlot = 'generation' | 'upscale'
type ScaleFactor = 2 | 4

const SEEDVR_BATCH_SIZES = [1, 5, 9, 13] as const
const SEEDVR_OVERLAPS = [0, 1, 2, 3, 4] as const

const SEEDVR_MODEL_OPTIONS: readonly { id: SeedVrModel; label: string }[] = [
    { id: '3b', label: '3B · быстрее, меньше памяти' },
    { id: '7b', label: '7B · качество' },
    { id: '7b_sharp', label: '7B Sharp · резче' },
]

interface JobState {
    status: JobStatus
    progress: MockProgressEvent | null
    result: Blob | null
    error: string | null
    realProgress: RealJobProgress | null
    startedAtMs: number | null
    finishedAtMs: number | null
    cancelling: boolean
}

interface UploadSurfaceProps {
    id: string
    title: string
    description: string
    acceptedFormats: string
    accept: string
    multiple?: boolean
    disabled: boolean
    error: string | null
    onFiles: (files: File[]) => void
}

interface SelectedVideoProps {
    file: File
    disabled: boolean
    onRemove: () => void
}

interface SelectedAudioProps {
    file: File
    index: number
    disabled: boolean
    onRemove: () => void
}

interface ImageReferenceProps {
    file: File
    index: number
    disabled: boolean
    onRemove: () => void
}

interface ProgressPanelProps {
    realConnection: boolean
    mode: OperationMode
    job: JobState
    filename: string
    scale: ScaleFactor
    onCancel: () => void
}

const PIKA_MAX_REFERENCE_IMAGES = 9

const FAMILY_HEADINGS: Record<ModelFamily, string> = {
    h3: 'MiniMax H3',
    'h3-turbo': 'MiniMax H3 Turbo',
    pika: 'Pika H3 Flex',
    seedvr2: 'SeedVR2',
    'flashvsr-turbo': 'FlashVSR+ Turbo',
    'flashvsr-base': 'FlashVSR+ Base',
    'realesrgan-x2plus': 'RealESRGAN x2plus',
    'realesrgan-animevideo': 'RealESRGAN AnimeVideo-v3',
    interpolation: 'Повышение FPS',
}

const FAMILY_NOTES: Record<ModelFamily, string> = {
    h3: 'Ref2V · 20 шагов · до 9 изображений · optional reference-video',
    'h3-turbo': 'Ref2V · Turbo LoRA · 4 шага · до 9 изображений · optional reference-video',
    pika: 'Ref2VA · 6 шагов · до 9 изображений · optional reference-video (движение)',
    seedvr2: 'SeedVR2 3B / 7B / 7B Sharp · ×2 / ×4 · preserve source FPS/audio',
    'flashvsr-turbo': 'FlashVSR+ Turbo · ×2 / ×4 · быстрый · preserve source FPS/audio',
    'flashvsr-base': 'FlashVSR+ Base · ×2 / ×4 · качественнее, медленнее · preserve source FPS/audio',
    'realesrgan-x2plus': 'RealESRGAN x2plus · фиксированный ×2 · общий случай · preserve source FPS/audio',
    'realesrgan-animevideo': 'RealESRGAN AnimeVideo-v3 · фиксированный ×4 · аниме · preserve source FPS/audio',
    interpolation: 'FILM / RIFE · ×2 / ×3 кадров · та же длительность · звук сохраняется',
}

const GENERATION_MODE_TITLES: Record<GenerationVariant, string> = {
    h3: 'Base',
    'h3-turbo': 'Turbo LoRA',
    pika: 'Pika H3 Flex',
}

const GENERATION_MODE_NOTES: Record<GenerationVariant, string> = {
    h3: '20 шагов · без Turbo LoRA',
    'h3-turbo': '4 шага · быстрый режим',
    pika: '6 шагов · расписание из манифеста адаптера',
}

const UPSCALE_MODEL_FAMILIES: Partial<Record<ModelFamily, { key: UpscaleModelKey; bundleId: string; scale: ScaleFactor }>> = {
    'realesrgan-x2plus': { key: 'realesrgan_x2plus', bundleId: 'realesrgan_x2plus', scale: 2 },
    'realesrgan-animevideo': { key: 'realesrgan_animevideo_v3', bundleId: 'realesrgan_animevideo_v3', scale: 4 },
}

const INTERPOLATION_MODEL_OPTIONS: readonly { id: InterpolationModel; label: string }[] = [
    { id: 'rife_4_26', label: 'RIFE 4.26' },
    { id: 'rife_4_25', label: 'RIFE 4.25' },
    { id: 'film', label: 'FILM' },
]
const INTERPOLATION_MULTIPLIERS: readonly (2 | 3)[] = [2, 3]

const MODEL_FAMILY_TABS: readonly { id: ModelFamily; label: string }[] = [
    { id: 'h3', label: 'MiniMax H3' },
    { id: 'h3-turbo', label: 'MiniMax H3 Turbo' },
    { id: 'pika', label: 'Pika H3 Flex' },
    { id: 'seedvr2', label: 'SeedVR2' },
    { id: 'flashvsr-turbo', label: 'FlashVSR+ Turbo' },
    { id: 'flashvsr-base', label: 'FlashVSR+ Base' },
    { id: 'realesrgan-x2plus', label: 'RealESRGAN x2plus' },
    { id: 'realesrgan-animevideo', label: 'RealESRGAN AnimeVideo-v3' },
    { id: 'interpolation', label: 'Повышение FPS' },
]

function createIdleJob(): JobState {
    return {
        status: 'idle',
        progress: null,
        result: null,
        error: null,
        realProgress: null,
        startedAtMs: null,
        finishedAtMs: null,
        cancelling: false,
    }
}

function formatDownloadSpeed(bytesPerSecond: number | null): string {
    if (bytesPerSecond === null || !Number.isFinite(bytesPerSecond) || bytesPerSecond <= 0) {
        return 'рассчитывается…'
    }
    const kibibytesPerSecond = bytesPerSecond / 1024
    const formatter = new Intl.NumberFormat('ru-RU', {
        maximumFractionDigits: kibibytesPerSecond < 1_024 ? 0 : 1,
    })
    const value = kibibytesPerSecond < 1_024 ? kibibytesPerSecond : kibibytesPerSecond / 1_024
    const unit = kibibytesPerSecond < 1_024 ? 'КиБ/с' : 'МиБ/с'
    return `${formatter.format(value)} ${unit}`
}

function formatDownloadEta(secondsRemaining: number | null): string {
    if (secondsRemaining === null || !Number.isFinite(secondsRemaining) || secondsRemaining <= 0) {
        return 'рассчитывается…'
    }
    const totalSeconds = Math.ceil(secondsRemaining)
    if (totalSeconds < 60) {
        return `~${totalSeconds} с`
    }
    const totalMinutes = Math.ceil(totalSeconds / 60)
    if (totalMinutes < 60) {
        return `~${totalMinutes} мин`
    }
    const hours = Math.floor(totalMinutes / 60)
    const minutes = totalMinutes % 60
    return minutes === 0 ? `~${hours} ч` : `~${hours} ч ${minutes} мин`
}

function App() {
    const [realConnection, setRealConnection] = createSignal<boolean | null>(null)
    const [modelBundles, setModelBundles] = createSignal<ModelBundlesSnapshot | null>(null)
    const [downloadProgressSamples, setDownloadProgressSamples] = createSignal<readonly ModelDownloadProgressSample[]>([])
    const [activeFamily, setActiveFamily] = createSignal<ModelFamily>('h3')
    const [bundleActionError, setBundleActionError] = createSignal<string | null>(null)
    const [bundleStatusLoading, setBundleStatusLoading] = createSignal(true)
    const [mockPreviewFamily, setMockPreviewFamily] = createSignal<ModelFamily | null>(null)
    const [referenceVideo, setReferenceVideo] = createSignal<File | null>(null)
    const [referenceImages, setReferenceImages] = createSignal<File[]>([])
    const [referenceAudios, setReferenceAudios] = createSignal<File[]>([])
    const [generationPrompt, setGenerationPrompt] = createSignal('')
    const [negativePrompt, setNegativePrompt] = createSignal('')
    const [negativeStrength, setNegativeStrength] = createSignal<number>(NEGATIVE_DEFAULT_STRENGTH)
    const [generationDuration, setGenerationDuration] = createSignal<number>(H3_DURATION_OPTIONS[0]?.seconds ?? 0)
    const [generationResolution, setGenerationResolution] = createSignal<H3Resolution>('standard')
    const [refImageSize, setRefImageSize] = createSignal<'match' | 'max'>('match')
    const turboLoraEnabled = () => activeFamily() === 'h3-turbo'
    const flashVsrVariant = (): FlashVsrVariant | null => {
        const family = activeFamily()
        return family === 'flashvsr-turbo' ? 'turbo' : family === 'flashvsr-base' ? 'base' : null
    }
    const upscaleModelFamily = () => UPSCALE_MODEL_FAMILIES[activeFamily()] ?? null
    const isInterpolation = () => activeFamily() === 'interpolation'
    const isGenerationFamily = () => activeFamily() !== 'seedvr2' && flashVsrVariant() === null && upscaleModelFamily() === null && !isInterpolation()
    const upscaleLabel = () => isInterpolation() ? 'моделью интерполяции кадров' : FAMILY_HEADINGS[activeFamily()]
    const [interpolationModel, setInterpolationModel] = createSignal<InterpolationModel>('rife_4_26')
    const [interpolationMultiplier, setInterpolationMultiplier] = createSignal<2 | 3>(2)
    const maxReferenceImages = () => activeFamily() === 'pika' ? PIKA_MAX_REFERENCE_IMAGES : MAX_REFERENCE_IMAGES
    const [upscaleVideo, setUpscaleVideo] = createSignal<File | null>(null)
    const [upscaleFactor, setUpscaleFactor] = createSignal<ScaleFactor>(2)
    const [seedVrModel, setSeedVrModel] = createSignal<SeedVrModel>('7b_sharp')
    const [seedVrBatch, setSeedVrBatch] = createSignal(1)
    const [seedVrOverlap, setSeedVrOverlap] = createSignal(0)
    const [generationVideoError, setGenerationVideoError] = createSignal<string | null>(null)
    const [generationImageError, setGenerationImageError] = createSignal<string | null>(null)
    const [generationAudioError, setGenerationAudioError] = createSignal<string | null>(null)
    const [upscaleVideoError, setUpscaleVideoError] = createSignal<string | null>(null)
    const [generationJob, setGenerationJob] = createSignal<JobState>(createIdleJob())
    const [upscaleJob, setUpscaleJob] = createSignal<JobState>(createIdleJob())

    let startupController: AbortController | null = null
    let modelActionController: AbortController | null = null
    const activeJobControllers = new Set<AbortController>()

    const recordDownloadProgress = (snapshot: ModelBundlesSnapshot): void => {
        const activeBundle = snapshot.bundles.find((bundle) => bundle.id === snapshot.activeDownloadBundleId)
        if (activeBundle === undefined || activeBundle.phase !== 'downloading') {
            setDownloadProgressSamples([])
            return
        }

        const timestampMs = window.performance.now()
        const sample: ModelDownloadProgressSample = {
            timestampMs,
            transferredBytes: activeBundle.totalBytes - activeBundle.pendingBytes,
        }
        setDownloadProgressSamples((samples) => {
            const latest = samples.at(-1)
            if (latest !== undefined && sample.transferredBytes < latest.transferredBytes) {
                return [sample]
            }
            const windowStart = timestampMs - MODEL_DOWNLOAD_RATE_WINDOW_MS
            return [...samples.filter((item) => item.timestampMs >= windowStart), sample]
        })
    }

    const updateModelBundles = async (signal: AbortSignal): Promise<ModelBundlesSnapshot> => {
        const snapshot = await getModelBundles(signal)
        setModelBundles(snapshot)
        recordDownloadProgress(snapshot)
        return snapshot
    }

    const pollModelBundles = async (signal: AbortSignal): Promise<void> => {
        let snapshot = modelBundles()
        let nextPollAt = window.performance.now()
        while (!signal.aborted && snapshot !== null && needsModelBundlePolling(snapshot)) {
            nextPollAt += MODEL_BUNDLE_POLL_INTERVAL_MS
            const delayMs = Math.max(0, nextPollAt - window.performance.now())
            await new Promise<void>((resolve) => window.setTimeout(resolve, delayMs))
            if (signal.aborted) {
                return
            }
            snapshot = await updateModelBundles(signal)
            nextPollAt = Math.max(nextPollAt, window.performance.now())
        }
    }

    const loadWorkbench = () => {
        startupController?.abort()
        const controller = new AbortController()
        startupController = controller
        setBundleStatusLoading(true)
        setBundleActionError(null)

        void (async () => {
            const config = await getRuntimeConfig(controller.signal)
            setRealConnection(config.realConnection)
            const snapshot = await updateModelBundles(controller.signal)
            setBundleStatusLoading(false)
            await pollModelBundles(controller.signal)
            return snapshot
        })().catch((error: unknown) => {
            if (isAbortError(error)) {
                return
            }
            setBundleStatusLoading(false)
            setBundleActionError(error instanceof Error ? error.message : 'Не удалось проверить model bundles.')
        })
    }

    const startBundleDownload = (bundleId: string) => {
        if (realConnection() !== true) {
            return
        }
        modelActionController?.abort()
        const controller = new AbortController()
        modelActionController = controller
        setBundleActionError(null)
        void (async () => {
            await startModelBundleDownload(bundleId, controller.signal)
            const snapshot = await updateModelBundles(controller.signal)
            await pollModelBundles(controller.signal)
            return snapshot
        })().catch((error: unknown) => {
            if (!isAbortError(error)) {
                setBundleActionError(error instanceof Error ? error.message : 'Загрузка model bundle завершилась ошибкой.')
            }
        })
    }

    const stopBundleDownload = (bundleId: string) => {
        modelActionController?.abort()
        const controller = new AbortController()
        modelActionController = controller
        setBundleActionError(null)
        void cancelModelBundleDownload(bundleId, controller.signal)
            .then(async () => updateModelBundles(controller.signal))
            .catch((error: unknown) => {
                if (!isAbortError(error)) {
                    setBundleActionError(error instanceof Error ? error.message : 'Не удалось остановить загрузку.')
                }
            })
    }

    const deleteBundle = (bundle: ModelBundle) => {
        if (!realConnection() || !bundle.canDelete) {
            return
        }
        const removableAssets = bundle.assets.filter((asset) => !asset.shared
            && (asset.status !== 'waiting' || asset.downloadedBytes > 0))
        if (removableAssets.length === 0) {
            return
        }
        const files = removableAssets.map((asset) => asset.target).join('\n')
        const bytes = removableAssets.reduce((total, asset) => total + asset.storageBytes, 0)
        const preserved = bundle.assets.filter((asset) => asset.shared).length
        const detail = preserved > 0 ? `\n${preserved} общих файлов будут сохранены.` : ''
        if (!window.confirm(`Удалить файлы только комплекта ${bundle.title}?\n${files}\nОсвободится до ${formatBytes(bytes)}.${detail}`)) {
            return
        }
        const controller = new AbortController()
        modelActionController = controller
        setBundleActionError(null)
        void deleteModelBundle(bundle.id, controller.signal)
            .then(async () => updateModelBundles(controller.signal))
            .catch((error: unknown) => {
                if (!isAbortError(error)) {
                    setBundleActionError(error instanceof Error ? error.message : 'Не удалось удалить model bundle.')
                }
            })
    }

    const selectedBundleId = () => {
        if (activeFamily() === 'h3') {
            return 'h3_base'
        }
        if (activeFamily() === 'h3-turbo') {
            return 'h3_turbo'
        }
        if (activeFamily() === 'pika') {
            return 'pika_h3_flex_ref2va'
        }
        if (isInterpolation()) {
            return 'frame_interpolation'
        }
        const modelFamily = upscaleModelFamily()
        if (modelFamily !== null) {
            return modelFamily.bundleId
        }
        const variant = flashVsrVariant()
        return variant === null ? 'seedvr2_upscale' : `flashvsr_plus_${variant}`
    }

    const selectedBundle = () => modelBundles()?.bundles.find((bundle) => bundle.id === selectedBundleId())
    const selectedBundleTelemetry = (): ModelDownloadTelemetry | null => {
        const bundle = selectedBundle()
        if (bundle === undefined || bundle.phase !== 'downloading'
            || modelBundles()?.activeDownloadBundleId !== bundle.id) {
            return null
        }
        return calculateModelDownloadTelemetry(
            downloadProgressSamples(),
            bundle.pendingBytes,
            window.performance.now(),
        )
    }

    const selectGenerationVideo = (files: File[]) => {
        const file = files[0]
        if (file === undefined) {
            return
        }

        const error = validateVideoFile(file)
        if (error !== null) {
            setGenerationVideoError(error)
            return
        }

        setReferenceVideo(file)
        setGenerationVideoError(null)
        setGenerationJob(createIdleJob())
    }

    const selectUpscaleVideo = (files: File[]) => {
        const file = files[0]
        if (file === undefined) {
            return
        }

        const error = validateVideoFile(file)
        if (error !== null) {
            setUpscaleVideoError(error)
            return
        }

        setUpscaleVideo(file)
        setUpscaleVideoError(null)
        setUpscaleJob(createIdleJob())
    }

    const selectReferenceImages = (files: File[]) => {
        const validation = validateImageBatch(referenceImages().length, files, maxReferenceImages())
        if (validation.accepted.length > 0) {
            setReferenceImages((current) => [...current, ...validation.accepted])
            setGenerationJob(createIdleJob())
        }

        setGenerationImageError(validation.rejected.length === 0
            ? null
            : validation.rejected.map((rejection) => `${rejection.name}: ${rejection.reason}`).join(' '))
    }

    const selectReferenceAudios = (files: File[]) => {
        const validation = validateAudioBatch(referenceAudios().length, files)
        if (validation.accepted.length > 0) {
            setReferenceAudios((current) => [...current, ...validation.accepted])
            setGenerationJob(createIdleJob())
        }

        setGenerationAudioError(validation.rejected.length === 0
            ? null
            : validation.rejected.map((rejection) => `${rejection.name}: ${rejection.reason}`).join(' '))
    }

    const slotControllers = new Map<JobSlot, AbortController>()
    const jobSetters: Record<JobSlot, Setter<JobState>> = { generation: setGenerationJob, upscale: setUpscaleJob }
    const jobGetters: Record<JobSlot, () => JobState> = { generation: generationJob, upscale: upscaleJob }

    const finishJob = (slot: JobSlot, patch: Partial<JobState>) => {
        slotControllers.delete(slot)
        jobSetters[slot]((job) => ({ ...job, cancelling: false, finishedAtMs: job.finishedAtMs ?? Date.now(), ...patch }))
    }

    const startJob = (
        slot: JobSlot,
        options: DemoVideoOptions,
    ) => {
        const controller = new AbortController()
        activeJobControllers.add(controller)
        slotControllers.set(slot, controller)
        jobSetters[slot]({ ...createIdleJob(), status: 'running', startedAtMs: Date.now() })

        void runMockPipeline(
            options.mode,
            (progress) => jobSetters[slot]((current) => ({ ...current, progress })),
            (reportProgress) => createDemoWebM(options, reportProgress),
            controller.signal,
        )
            .then((result) => finishJob(slot, { status: 'complete', result, error: null }))
            .catch((error: unknown) => {
                if (isAbortError(error)) {
                    return
                }
                finishJob(slot, { status: 'error', error: error instanceof Error ? error.message : 'Mock-задача завершилась с ошибкой.' })
            })
            .finally(() => activeJobControllers.delete(controller))
    }

    const startRealJob = (options: RealJobOptions, slot: JobSlot) => {
        const controller = new AbortController()
        activeJobControllers.add(controller)
        slotControllers.set(slot, controller)
        jobSetters[slot]({ ...createIdleJob(), status: 'running', startedAtMs: Date.now() })
        void runRealJob(options, (realProgress) => jobSetters[slot]((job) => ({ ...job, realProgress })), controller.signal)
            .then((realProgress) => finishJob(slot, { status: realProgress.status === 'cancelled' ? 'cancelled' : 'complete', realProgress }))
            .catch((error: unknown) => {
                if (!isAbortError(error)) {
                    finishJob(slot, { status: 'error', error: error instanceof Error ? error.message : 'ComfyUI job завершился с ошибкой.' })
                }
            })
            .finally(() => activeJobControllers.delete(controller))
    }

    const cancelJob = (slot: JobSlot) => {
        const job = jobGetters[slot]()
        if (job.status !== 'running' || job.cancelling) {
            return
        }
        const controller = slotControllers.get(slot)
        const jobId = job.realProgress?.id ?? null
        jobSetters[slot]((current) => ({ ...current, cancelling: true }))
        if (realConnection() !== true || jobId === null) {
            controller?.abort()
            finishJob(slot, { status: 'cancelled', error: null })
            return
        }
        void cancelRealJob(jobId)
            .then((realProgress) => {
                finishJob(slot, { status: 'cancelled', realProgress, error: null })
                controller?.abort()
            })
            .catch((error: unknown) => {
                jobSetters[slot]((current) => ({
                    ...current,
                    cancelling: false,
                    error: error instanceof Error ? error.message : 'Не удалось отменить задачу.',
                }))
            })
    }

    const startGeneration = () => {
        const requestedDuration = generationDuration()
        const generatedDuration = h3GeneratedDurationSeconds(requestedDuration)
        if (!isPromptValid(generationPrompt())
            || generatedDuration === null
            || (realConnection() && !isNegativeInputValid(negativePrompt(), negativeStrength()))
            || generationJob().status === 'running') {
            return
        }

        if (realConnection()) {
            if (activeFamily() === 'pika') {
                if (selectedBundle()?.executionEnabled !== true) {
                    return
                }
                startRealJob({
                    mode: 'pika',
                    prompt: generationPrompt().trim(),
                    images: referenceImages(),
                    video: referenceVideo(),
                    audios: referenceAudios(),
                    durationSeconds: requestedDuration,
                    resolution: generationResolution(),
                    seed: Math.floor(Math.random() * Number.MAX_SAFE_INTEGER),
                    refImageSize: refImageSize(),
                    negativePrompt: negativePrompt(),
                    negativeStrength: negativeStrength(),
                }, 'generation')
                return
            }
            startRealJob({
                mode: 'generation',
                prompt: generationPrompt().trim(),
                images: referenceImages(),
                video: referenceVideo(),
                audios: referenceAudios(),
                durationSeconds: requestedDuration,
                resolution: generationResolution(),
                turboLora: turboLoraEnabled(),
                negativePrompt: negativePrompt(),
                negativeStrength: negativeStrength(),
            }, 'generation')
            return
        }
        startJob('generation', {
            mode: 'generation',
            referenceVideo: referenceVideo(),
            referenceImage: referenceImages()[0] ?? null,
            prompt: generationPrompt().trim(),
            durationSeconds: generatedDuration,
        })
    }

    const startUpscale = () => {
        const file = upscaleVideo()
        if (file === null || upscaleJob().status === 'running') {
            return
        }

        if (realConnection() && isInterpolation()) {
            startRealJob({ mode: 'interpolate', prompt: '', images: [], video: file, model: interpolationModel(), multiplier: interpolationMultiplier() }, 'upscale')
            return
        }
        const modelFamily = upscaleModelFamily()
        if (realConnection() && modelFamily !== null) {
            startRealJob({ mode: 'upscale_model', prompt: '', images: [], video: file, model: modelFamily.key }, 'upscale')
            return
        }
        const variant = flashVsrVariant()
        if (realConnection() && variant !== null) {
            startRealJob({ mode: 'flashvsr', prompt: '', images: [], video: file, scale: upscaleFactor(), variant }, 'upscale')
            return
        }
        if (realConnection()) {
            startRealJob({ mode: 'upscale', prompt: '', images: [], video: file, scale: upscaleFactor(), model: seedVrModel(), batchSize: seedVrBatch(), temporalOverlap: Math.min(seedVrOverlap(), seedVrBatch() - 1) }, 'upscale')
            return
        }
        startJob('upscale', {
            mode: 'upscale',
            referenceVideo: file,
            referenceImage: null,
            prompt: '',
            durationSeconds: null,
            scale: upscaleModelFamily()?.scale ?? upscaleFactor(),
        })
    }

    onMount(loadWorkbench)
    onCleanup(() => {
        startupController?.abort()
        modelActionController?.abort()
        for (const controller of activeJobControllers) {
            controller.abort()
        }
    })

    const readyBundleCount = () => modelBundles()?.bundles.filter((bundle) => bundle.phase === 'ready').length ?? 0
    const isGenerationRunning = () => generationJob().status === 'running'
    const isUpscaleRunning = () => upscaleJob().status === 'running'
    const activeBundleDownload = () => modelBundles()?.activeDownloadBundleId === selectedBundleId()
    const isWorkflowReady = (bundle: ModelBundle) => bundle.phase === 'ready'
        || (realConnection() === false && mockPreviewFamily() === activeFamily())

    return (
        <div class="min-h-dvh bg-paper text-ink">
            <header class="app-header">
                <a class="brand-mark" href="#" onClick={(event) => event.preventDefault()} aria-label="Видеомастерская">
                    <span class="brand-mark__shape" aria-hidden="true" />
                    <span>Видеомастерская</span>
                </a>
                <div class="header-actions">
                    <span class="mode-indicator">
                        <span class="mode-indicator__dot" aria-hidden="true" />
                        {realConnection() === null ? 'подключаю мастерскую…' : realConnection() ? 'Vast · реальные модели и GPU' : 'локальный mock · без загрузок'}
                    </span>
                    <span class="bundle-count">{readyBundleCount()} / {modelBundles()?.bundles.length ?? 0} комплектов проверено</span>
                </div>
            </header>

            <main class="app-main">
                <section class="model-workbench" aria-label="Модельная видеомастерская">
                    <div class="model-workbench__heading">
                        <div>
                            <p class="section-kicker">Рабочая область</p>
                            <h1>Мастерская моделей</h1>
                            <p>Каждая вкладка использует свой lock-listed комплект. Переключение вкладок ничего не загружает.</p>
                        </div>
                        <button class={buttonStyles({ tone: 'quiet', size: 'small' })} type="button" onClick={loadWorkbench}>
                            Обновить статус
                        </button>
                    </div>

                    <div class="model-family-tabs" role="tablist" aria-label="Видеомодель">
                        <For each={MODEL_FAMILY_TABS}>
                            {(tab) => (
                                <button
                                    class="model-family-tab"
                                    classList={{ 'model-family-tab--active': activeFamily() === tab.id }}
                                    type="button"
                                    role="tab"
                                    id={`family-tab-${tab.id}`}
                                    aria-selected={activeFamily() === tab.id}
                                    aria-controls="model-family-panel"
                                    tabIndex={activeFamily() === tab.id ? 0 : -1}
                                    onClick={() => {
                                        setActiveFamily(tab.id)
                                        setMockPreviewFamily(null)
                                    }}
                                >
                                    {tab.label}
                                </button>
                            )}
                        </For>
                    </div>

                    <section id="model-family-panel" class="model-family-panel" role="tabpanel" aria-labelledby={`family-tab-${activeFamily()}`}>
                        <Show when={bundleStatusLoading()}>
                            <p class="bundle-status-note" role="status">Читаю только состояние manifest; скачивание не запускается.</p>
                        </Show>
                        <Show when={bundleActionError()}>
                            {(error) => <p class="bundle-error" role="alert">{error()}</p>}
                        </Show>
                        <Show when={modelBundles() !== null && !bundleStatusLoading()} fallback={
                            <div class="bundle-empty-state">
                                <p>{bundleActionError() ?? 'Backend пока не вернул состояние model bundles.'}</p>
                                <button class={buttonStyles({ tone: 'secondary' })} type="button" onClick={loadWorkbench}>Повторить проверку</button>
                            </div>
                        }>
                            <Show when={selectedBundle()} fallback={<p role="status">Выбранный bundle отсутствует в manifest.</p>}>
                                {(bundle) => (
                                    <>
                                        <div class="bundle-heading">
                                            <div>
                                                <p class="section-kicker">{bundle().runtime} · {bundle().workflows.join(' / ')}</p>
                                                <h2>{bundle().title}</h2>
                                            </div>
                                            <span class={statusStyles({ tone: bundle().phase === 'ready' ? 'ready' : bundle().phase === 'error' ? 'error' : bundle().phase === 'waiting' ? 'waiting' : 'active' })}>
                                                {bundle().phase === 'ready' ? 'Файлы проверены' : bundle().phase === 'verifying' ? 'Проверка SHA-256' : bundle().phase === 'downloading' ? 'Загрузка' : bundle().phase === 'error' ? 'Ошибка' : 'Не загружено'}
                                            </span>
                                        </div>

                                        <Show when={isWorkflowReady(bundle())} fallback={
                                            <ModelBundleSetup
                                                bundle={bundle()}
                                                realConnection={realConnection() === true}
                                                error={bundleActionError()}
                                                onDownload={() => startBundleDownload(bundle().id)}
                                                onCancel={() => stopBundleDownload(bundle().id)}
                                                onDelete={() => deleteBundle(bundle())}
                                                onMockPreview={() => setMockPreviewFamily(activeFamily())}
                                                isActiveDownload={activeBundleDownload()}
                                                telemetry={selectedBundleTelemetry()}
                                            />
                                        }>
                                            <Show when={true}>
                                                <Studio
                                                    realConnection={realConnection() === true}
                                                    activeTab={isGenerationFamily() ? 'generation' : 'upscale'}
                                                    setActiveTab={() => undefined}
                                                    showModeTabs={false}
                                                    heading={FAMILY_HEADINGS[activeFamily()]}
                                                    headingNote={FAMILY_NOTES[activeFamily()]}
                                                    referenceVideo={referenceVideo()}
                                                    referenceImages={referenceImages()}
                                                    referenceAudios={referenceAudios()}
                                                    generationAudioError={generationAudioError()}
                                                    generationPrompt={generationPrompt()}
                                                    negativePrompt={negativePrompt()}
                                                    negativeStrength={negativeStrength()}
                                                    onNegativePrompt={setNegativePrompt}
                                                    onNegativeStrength={setNegativeStrength}
                                                    generationDuration={generationDuration()}
                                                    generationResolution={generationResolution()}
                                                    generationVariant={isGenerationFamily() ? activeFamily() as GenerationVariant : 'h3'}
                                                    maxReferenceImages={maxReferenceImages()}
                                                    refImageSize={refImageSize()}
                                                    onRefImageSize={setRefImageSize}
                                                    blockedReason={selectedBundle()?.executionEnabled === false ? selectedBundle()?.executionBlockedReason ?? 'Запуск заблокирован.' : null}
                                                    generationVideoError={generationVideoError()}
                                                    generationImageError={generationImageError()}
                                                    upscaleVideo={upscaleVideo()}
                                                    upscaleFactor={upscaleModelFamily()?.scale ?? upscaleFactor()}
                                                    fixedScale={upscaleModelFamily() !== null}
                                                    upscaleLabel={upscaleLabel()}
                                                    showSeedVrOptions={activeFamily() === 'seedvr2'}
                                                    showInterpolationOptions={isInterpolation()}
                                                    interpolationModel={interpolationModel()}
                                                    interpolationMultiplier={interpolationMultiplier()}
                                                    onInterpolationModel={setInterpolationModel}
                                                    onInterpolationMultiplier={setInterpolationMultiplier}
                                                    runLabel={isInterpolation() ? `Повысить FPS ×${interpolationMultiplier()}` : ''}
                                                    seedVrModel={seedVrModel()}
                                                    onSeedVrModel={setSeedVrModel}
                                                    seedVrBatch={seedVrBatch()}
                                                    onSeedVrBatch={setSeedVrBatch}
                                                    seedVrOverlap={seedVrOverlap()}
                                                    onSeedVrOverlap={setSeedVrOverlap}
                                                    upscaleVideoError={upscaleVideoError()}
                                                    generationJob={generationJob()}
                                                    upscaleJob={upscaleJob()}
                                                    isGenerationRunning={isGenerationRunning()}
                                                    isUpscaleRunning={isUpscaleRunning()}
                                                    onGenerationVideo={selectGenerationVideo}
                                                    onReferenceImages={selectReferenceImages}
                                                    onReferenceAudios={selectReferenceAudios}
                                                    onRemoveReferenceAudio={(file) => {
                                                        setReferenceAudios((current) => current.filter((candidate) => candidate !== file))
                                                        setGenerationAudioError(null)
                                                        setGenerationJob(createIdleJob())
                                                    }}
                                                    onRemoveGenerationVideo={() => {
                                                        setReferenceVideo(null)
                                                        setGenerationVideoError(null)
                                                        setGenerationJob(createIdleJob())
                                                    }}
                                                    onRemoveReferenceImage={(file) => {
                                                        setReferenceImages((current) => current.filter((candidate) => candidate !== file))
                                                        setGenerationImageError(null)
                                                        setGenerationJob(createIdleJob())
                                                    }}
                                                    onGenerationPrompt={setGenerationPrompt}
                                                    onGenerationPromptEdited={() => setGenerationJob(createIdleJob())}
                                                    onGenerationResolution={(resolution) => {
                                                        setGenerationResolution(resolution)
                                                        setGenerationJob(createIdleJob())
                                                    }}
                                                    onGenerationDuration={(duration) => {
                                                        setGenerationDuration(duration)
                                                        setGenerationJob(createIdleJob())
                                                    }}
                                                    onUpscaleVideo={selectUpscaleVideo}
                                                    onRemoveUpscaleVideo={() => {
                                                        setUpscaleVideo(null)
                                                        setUpscaleVideoError(null)
                                                        setUpscaleJob(createIdleJob())
                                                    }}
                                                    onUpscaleFactor={setUpscaleFactor}
                                                    onStartGeneration={startGeneration}
                                                    onCancelGeneration={() => cancelJob('generation')}
                                                    onCancelUpscale={() => cancelJob('upscale')}
                                                    onStartUpscale={startUpscale}
                                                />
                                            </Show>
                                        </Show>
                                    </>
                                )}
                            </Show>
                        </Show>
                    </section>
                </section>
            </main>

            <footer class="app-footer">
                <span>{realConnection() ? 'Vast · реальные model bundles' : 'Локальный mock · сеть, файлы и GPU не используются'}</span>
                <span>{realConnection() ? 'Модели удаляются только вручную после подтверждения' : 'Симуляция состояния не подтверждает наличие весов'}</span>
            </footer>
        </div>
    )
}


interface ModelBundleSetupProps {
    bundle: ModelBundle
    realConnection: boolean
    error: string | null
    isActiveDownload: boolean | undefined
    telemetry: ModelDownloadTelemetry | null
    onDownload: () => void
    onCancel: () => void
    onDelete: () => void
    onMockPreview: () => void
}

function ModelBundleSetup(props: ModelBundleSetupProps) {
    const hasRemovableAssets = () => props.bundle.assets.some((asset) => !asset.shared
        && (asset.status !== 'waiting' || asset.downloadedBytes > 0))
    const statusLabel = (status: ModelBundle['assets'][number]['status']) => {
        if (status === 'ready') return 'Готово · SHA-256'
        if (status === 'verifying') return 'Проверка SHA-256'
        if (status === 'downloading') return 'Загрузка'
        if (status === 'invalid') return 'Ошибка · требуется удалить вручную'
        return 'Не загружена'
    }

    return (
        <section class="bundle-setup" aria-label={`Комплект ${props.bundle.title}`}>
            <div class="bundle-setup__overview">
                <div>
                    <p class="meta-label">Состав выбранного workflow</p>
                    <p class="bundle-setup__size">{formatBytes(props.bundle.totalBytes)}</p>
                    <p class="field-help">Свободно {formatBytes(props.bundle.freeDiskBytes)} · требуется ещё {formatBytes(props.bundle.diskRequiredBytes)} с запасом.</p>
                </div>
                <strong>{props.bundle.downloadProgress}%</strong>
            </div>
            <ProgressBar label={`Загрузка ${props.bundle.title}`} value={props.bundle.downloadProgress} />
            <Show when={props.isActiveDownload && props.bundle.phase === 'downloading'}>
                <p class="bundle-setup__telemetry" aria-live="off" data-testid="model-download-telemetry">
                    Скорость: {formatDownloadSpeed(props.telemetry?.bytesPerSecond ?? null)}
                    <span aria-hidden="true"> · </span>
                    Осталось: {formatDownloadEta(props.telemetry?.secondsRemaining ?? null)}
                </p>
            </Show>
            <p class="bundle-setup__message" aria-live="polite">
                {props.error ?? props.bundle.error ?? props.bundle.currentMessage ?? (props.realConnection
                    ? 'Загрузка начинается только после нажатия кнопки.'
                    : 'Mock-режим не проверяет, не скачивает и не удаляет файлы моделей.')}
            </p>
            <div class="bundle-assets" aria-label="Файлы выбранного комплекта">
                <For each={props.bundle.assets}>
                    {(asset) => (
                        <article class="bundle-asset">
                            <div class="bundle-asset__details">
                                <div class="bundle-asset__title-row">
                                    <h3 title={asset.target}>{asset.target}</h3>
                                    <span class={statusStyles({ tone: asset.status === 'ready' ? 'ready' : asset.status === 'invalid' ? 'error' : asset.status === 'waiting' ? 'waiting' : 'active' })}>
                                        {statusLabel(asset.status)}
                                    </span>
                                </div>
                                <p>{formatBytes(asset.bytes)} · занято {formatBytes(asset.storageBytes)} · {asset.format} · {asset.loader}</p>
                                <p class="bundle-asset__source">{asset.repository}{asset.revision ? ` @ ${asset.revision.slice(0, 12)}` : ''}</p>
                                <Show when={asset.shared}>
                                    <p class="bundle-asset__shared">Общий файл: при удалении этого bundle сохранится.</p>
                                </Show>
                                <Show when={asset.error}>
                                    {(error) => <p class="bundle-error">{error()}</p>}
                                </Show>
                            </div>
                            <div class="bundle-asset__progress">
                                <span>{asset.progress}%</span>
                                <ProgressBar label={`Файл ${asset.target}`} value={asset.progress} compact />
                            </div>
                        </article>
                    )}
                </For>
            </div>
            <Show when={!props.bundle.diskSufficient && props.realConnection}>
                <p class="bundle-error" role="alert">Недостаточно свободного места для этого bundle и обязательного запаса.</p>
            </Show>
            <Show when={props.bundle.blockedReason}>
                {(reason) => <p class="bundle-error" role="alert">{reason()}</p>}
            </Show>
            <div class="bundle-setup__actions">
                <Show when={props.isActiveDownload}>
                    <button class={buttonStyles({ tone: 'secondary' })} type="button" onClick={() => props.onCancel()}>Остановить и сохранить частичные файлы</button>
                </Show>
                <Show when={!props.isActiveDownload}>
                    <button
                        class={buttonStyles({ tone: 'primary' })}
                        type="button"
                        disabled={!props.realConnection || !props.bundle.canDownload}
                        onClick={() => props.onDownload()}
                    >
                        {props.realConnection ? props.bundle.phase === 'error' ? 'Повторить загрузку' : 'Загрузить этот комплект' : 'Загрузка доступна только в реальном режиме'}
                    </button>
                </Show>
                <Show when={props.realConnection && hasRemovableAssets()}>
                    <button
                        class={buttonStyles({ tone: 'quiet' })}
                        type="button"
                        disabled={!props.bundle.canDelete}
                        title={props.bundle.deleteBlockedReason ?? undefined}
                        onClick={() => props.onDelete()}
                    >
                        Удалить файлы этого комплекта…
                    </button>
                </Show>
                <Show when={!props.realConnection}>
                    <button class={buttonStyles({ tone: 'secondary' })} type="button" onClick={() => props.onMockPreview()}>Открыть mock-форму без моделей</button>
                </Show>
            </div>
        </section>
    )
}

function ProgressBar(props: { label: string; value: number; compact?: boolean }) {
    const value = () => Math.min(Math.max(Math.round(props.value), 0), 100)

    return (
        <div
            class={`progress-track ${props.compact ? 'progress-track--compact' : ''}`}
            role="progressbar"
            aria-label={props.label}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={value()}
        >
            <span class="progress-track__fill" style={{ width: `${value()}%` }} />
        </div>
    )
}

interface StudioProps {
    realConnection: boolean
    activeTab: WorkspaceTab
    showModeTabs: boolean
    heading: string
    headingNote: string
    setActiveTab: (tab: WorkspaceTab) => void
    referenceVideo: File | null
    referenceImages: File[]
    referenceAudios: File[]
    generationAudioError: string | null
    onReferenceAudios: (files: File[]) => void
    onRemoveReferenceAudio: (file: File) => void
    generationPrompt: string
    negativePrompt: string
    negativeStrength: number
    onNegativePrompt: (text: string) => void
    onNegativeStrength: (strength: number) => void
    generationDuration: number
    generationResolution: H3Resolution
    generationVariant: GenerationVariant
    maxReferenceImages: number
    refImageSize: 'match' | 'max'
    onRefImageSize: (size: 'match' | 'max') => void
    blockedReason: string | null
    generationVideoError: string | null
    generationImageError: string | null
    upscaleVideo: File | null
    upscaleFactor: ScaleFactor
    fixedScale: boolean
    upscaleLabel: string
    showSeedVrOptions: boolean
    showInterpolationOptions: boolean
    interpolationModel: InterpolationModel
    interpolationMultiplier: 2 | 3
    onInterpolationModel: (model: InterpolationModel) => void
    onInterpolationMultiplier: (multiplier: 2 | 3) => void
    runLabel: string
    seedVrModel: SeedVrModel
    onSeedVrModel: (model: SeedVrModel) => void
    seedVrBatch: number
    onSeedVrBatch: (value: number) => void
    seedVrOverlap: number
    onSeedVrOverlap: (value: number) => void
    upscaleVideoError: string | null
    generationJob: JobState
    upscaleJob: JobState
    isGenerationRunning: boolean
    isUpscaleRunning: boolean
    onGenerationVideo: (files: File[]) => void
    onReferenceImages: (files: File[]) => void
    onRemoveGenerationVideo: () => void
    onRemoveReferenceImage: (file: File) => void
    onGenerationPrompt: (prompt: string) => void
    onGenerationPromptEdited: () => void
    onGenerationDuration: (duration: number) => void
    onGenerationResolution: (resolution: H3Resolution) => void
    onUpscaleVideo: (files: File[]) => void
    onRemoveUpscaleVideo: () => void
    onUpscaleFactor: (factor: ScaleFactor) => void
    onStartGeneration: () => void
    onCancelGeneration: () => void
    onCancelUpscale: () => void
    onStartUpscale: () => void
}

function Studio(props: StudioProps) {
    const generatedDuration = () => h3GeneratedDurationSeconds(props.generationDuration)
    const selectedResolution = () => H3_RESOLUTION_OPTIONS.find((option) => option.id === props.generationResolution) ?? { id: 'standard' as const, label: 'Стандарт', width: H3_OUTPUT_WIDTH, height: H3_OUTPUT_HEIGHT }
    const durationHelp = () => {
        const duration = generatedDuration()
        if (duration === null) {
            return 'Выбери длительность из списка.'
        }
        if (!props.realConnection) {
            return `Mock-демо будет длиться ${duration.toFixed(3)} с; H3 и разрешение не проверяются.`
        }
        return `H3 создаст ровно ${duration.toFixed(3)} с (сетка H3: 17×k+5 кадров, ${H3_FRAMES_PER_SECOND} fps); кадры не обрезаются. Разрешение: ${selectedResolution().width} × ${selectedResolution().height}.`
    }
    const generationCanRun = () => props.blockedReason === null
        && isPromptValid(props.generationPrompt)
        && (!props.realConnection || isNegativeInputValid(props.negativePrompt, props.negativeStrength))
        && h3FrameCountForDuration(props.generationDuration) !== null
        && !props.isGenerationRunning
    const upscaleCanRun = () => props.upscaleVideo !== null && !props.isUpscaleRunning

    const handleTabKeyDown: JSX.EventHandlerUnion<HTMLButtonElement, KeyboardEvent> = (event) => {
        let nextTab: WorkspaceTab | null = null
        if (event.key === 'ArrowRight') {
            nextTab = props.activeTab === 'generation' ? 'upscale' : 'generation'
        } else if (event.key === 'ArrowLeft') {
            nextTab = props.activeTab === 'upscale' ? 'generation' : 'upscale'
        }

        if (nextTab !== null) {
            event.preventDefault()
            props.setActiveTab(nextTab)
            document.getElementById(`tab-${nextTab}`)?.focus({ preventScroll: true })
        }
    }

    return (
        <section class="studio" aria-label={props.realConnection ? 'Видеомастерская Vast' : 'Mock-видеомастерская'}>
            <div class="studio-heading">
                <div>
                    <p class="section-kicker">Проверенный workflow</p>
                    <h1 id="studio-heading">{props.heading}</h1>
                </div>
                <p class="studio-heading__note">{props.headingNote}</p>
            </div>

            <Show when={props.showModeTabs}>
                <div class="workspace-tabs" role="tablist" aria-label="Режим обработки видео">
                    <button
                        id="tab-generation"
                        class="workspace-tab"
                        classList={{ 'workspace-tab--active': props.activeTab === 'generation' }}
                        type="button"
                        role="tab"
                        aria-selected={props.activeTab === 'generation'}
                        aria-controls="generation-panel"
                        tabIndex={props.activeTab === 'generation' ? 0 : -1}
                        onClick={() => props.setActiveTab('generation')}
                        onKeyDown={handleTabKeyDown}
                    >
                    Генерация
                    </button>
                    <button
                        id="tab-upscale"
                        class="workspace-tab"
                        classList={{ 'workspace-tab--active': props.activeTab === 'upscale' }}
                        type="button"
                        role="tab"
                        aria-selected={props.activeTab === 'upscale'}
                        aria-controls="upscale-panel"
                        tabIndex={props.activeTab === 'upscale' ? 0 : -1}
                        onClick={() => props.setActiveTab('upscale')}
                        onKeyDown={handleTabKeyDown}
                    >
                    Upscale
                    </button>
                    <p class="workspace-tabs__limit">{props.realConnection ? 'H3 Ref2V: референсы и видео' : 'H3 references в макете'}</p>
                </div>
            </Show>

            <section
                id="generation-panel"
                class="workspace-panel"
                role="tabpanel"
                aria-labelledby={props.showModeTabs ? 'tab-generation' : 'studio-heading'}
                tabIndex={0}
                hidden={props.activeTab !== 'generation'}
            >
                <div class="workspace-grid">
                    <div class="input-column">
                        <div class="section-title-row">
                            <div>
                                <h2>Входные материалы</h2>
                                <p>Референсы необязательны — достаточно промта.</p>
                            </div>
                            <span class={statusStyles({ tone: 'active' })}>{props.realConnection ? 'GPU' : 'Mock'}</span>
                        </div>

                        <div class="upload-stack">
                            <Show when={true}>
                                <div class="field-group">
                                    <UploadSurface
                                        id="generation-video"
                                        title="Референс-видео"
                                        description="Одно видео · MP4, WebM или MOV"
                                        acceptedFormats="ВИДЕО · 1 ФАЙЛ"
                                        accept="video/mp4,video/webm,video/quicktime,.m4v"
                                        disabled={props.isGenerationRunning}
                                        error={props.generationVideoError}
                                        onFiles={props.onGenerationVideo}
                                    />
                                    <Show when={props.referenceVideo} keyed>
                                        {(file) => (
                                            <SelectedVideo
                                                file={file}
                                                disabled={props.isGenerationRunning}
                                                onRemove={props.onRemoveGenerationVideo}
                                            />
                                        )}
                                    </Show>
                                </div>
                            </Show>

                            <div class="field-group">
                                <div class="field-title-row">
                                    <div>
                                        <label class="field-label" for="generation-audios">Аудио-референсы</label>
                                        <p id="generation-audios-help" class="field-help">WAV, MP3, FLAC, M4A, OGG или AAC · максимум {MAX_REFERENCE_AUDIOS} · в промте <code>&lt;Audio 1&gt;</code>…</p>
                                    </div>
                                    <span class="image-count">{props.referenceAudios.length} / {MAX_REFERENCE_AUDIOS}</span>
                                </div>
                                <UploadSurface
                                    id="generation-audios"
                                    title="Аудио-референсы"
                                    description="Перетащи сюда или выбери аудиофайлы"
                                    acceptedFormats="АУДИО"
                                    accept="audio/wav,audio/mpeg,audio/flac,audio/mp4,audio/ogg,audio/aac,.wav,.mp3,.flac,.m4a,.ogg,.aac"
                                    multiple
                                    disabled={props.isGenerationRunning || props.referenceAudios.length >= MAX_REFERENCE_AUDIOS}
                                    error={props.generationAudioError}
                                    onFiles={props.onReferenceAudios}
                                />
                                <Show when={props.referenceAudios.length > 0}>
                                    <div class="audio-reference-list" aria-label="Загруженные аудио-референсы">
                                        <For each={props.referenceAudios}>
                                            {(file, index) => (
                                                <SelectedAudio
                                                    file={file}
                                                    index={index() + 1}
                                                    disabled={props.isGenerationRunning}
                                                    onRemove={() => props.onRemoveReferenceAudio(file)}
                                                />
                                            )}
                                        </For>
                                    </div>
                                </Show>
                            </div>

                            <div class="field-group">
                                <div class="field-title-row">
                                    <div>
                                        <label class="field-label" for="generation-images">Референс-изображения</label>
                                        <p id="generation-images-help" class="field-help">JPG, PNG или WebP · максимум {props.maxReferenceImages}</p>
                                    </div>
                                    <span class="image-count">{props.referenceImages.length} / {props.maxReferenceImages}</span>
                                </div>
                                <UploadSurface
                                    id="generation-images"
                                    title="Референс-изображения"
                                    description="Перетащи сюда или выбери изображения"
                                    acceptedFormats="ИЗОБРАЖЕНИЯ"
                                    accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"
                                    multiple
                                    disabled={props.isGenerationRunning || props.referenceImages.length >= props.maxReferenceImages}
                                    error={props.generationImageError}
                                    onFiles={props.onReferenceImages}
                                />
                                <Show when={props.referenceImages.length > 0}>
                                    <div class="contact-sheet" aria-label="Загруженные референс-изображения">
                                        <For each={props.referenceImages}>
                                            {(file, index) => (
                                                <ImageReference
                                                    file={file}
                                                    index={index() + 1}
                                                    disabled={props.isGenerationRunning}
                                                    onRemove={() => props.onRemoveReferenceImage(file)}
                                                />
                                            )}
                                        </For>
                                        <Show when={props.referenceImages.length < props.maxReferenceImages}>
                                            <div class="contact-sheet__empty" aria-hidden="true">
                                                {props.maxReferenceImages - props.referenceImages.length} свободно
                                            </div>
                                        </Show>
                                    </div>
                                </Show>
                            </div>

                            <div class="field-group prompt-field">
                                <label class="field-label" for="generation-prompt">Промт</label>
                                <textarea
                                    id="generation-prompt"
                                    class="prompt-input"
                                    value={props.generationPrompt}
                                    onInput={(event) => {
                                        props.onGenerationPrompt(event.currentTarget.value)
                                        props.onGenerationPromptEdited()
                                    }}
                                    placeholder="Опиши сцену, движение камеры и звук…"
                                    rows={5}
                                    disabled={props.isGenerationRunning}
                                    aria-describedby="generation-prompt-help"
                                />
                                <p id="generation-prompt-help" class="field-help">
                                    {props.realConnection ? 'Промт обязателен. Обращайся к референсам как <Picture 1>, <Video 1> и далее по порядку.' : 'Промт обязателен. В mock-режиме он только попадёт в демо-кадр.'}
                                </p>
                            </div>
                            <Show when={props.realConnection}>
                                <div class="field-group negative-field">
                                    <label class="field-label" for="negative-prompt">Негативный промт (необязательно)</label>
                                    <input
                                        id="negative-prompt"
                                        class="prompt-input negative-input"
                                        type="text"
                                        value={props.negativePrompt}
                                        onInput={(event) => {
                                            props.onNegativePrompt(event.currentTarget.value)
                                            props.onGenerationPromptEdited()
                                        }}
                                        placeholder="Например: красное платье, надписи в кадре"
                                        maxlength={NEGATIVE_MAX_CHARS}
                                        disabled={props.isGenerationRunning}
                                        aria-describedby="negative-prompt-help"
                                        aria-invalid={negativePromptError(props.negativePrompt) !== null}
                                    />
                                    <label class="field-label" for="negative-strength">Сила негатива</label>
                                    <input
                                        id="negative-strength"
                                        class="prompt-input negative-input negative-input--number"
                                        type="number"
                                        min={NEGATIVE_MIN_STRENGTH}
                                        max={NEGATIVE_MAX_STRENGTH}
                                        step="0.5"
                                        value={props.negativeStrength}
                                        onInput={(event) => props.onNegativeStrength(event.currentTarget.valueAsNumber)}
                                        disabled={props.isGenerationRunning}
                                        aria-describedby="negative-prompt-help"
                                    />
                                    <p id="negative-prompt-help" class="field-help" role={negativeInputError(props.negativePrompt, props.negativeStrength) === null ? undefined : 'alert'}>
                                        {negativeInputError(props.negativePrompt, props.negativeStrength) ?? `NegPiP вычитает понятие из результата. Пиши 2–4 слова через запятую; сила ${NEGATIVE_MIN_STRENGTH}–${NEGATIVE_MAX_STRENGTH}, по умолчанию ${NEGATIVE_DEFAULT_STRENGTH}. Если эффект слабый, повысь силу или переформулируй.`}
                                    </p>
                                </div>
                            </Show>
                        </div>

                        <fieldset class="generation-settings" disabled={props.isGenerationRunning}>
                            <legend class="sr-only">Параметры генерации H3</legend>
                            <div class="field-group">
                                <label class="field-label" for="generation-duration">Длительность, секунд</label>
                                <select
                                    id="generation-duration"
                                    value={String(h3FrameCountForDuration(props.generationDuration) ?? '')}
                                    onChange={(event) => props.onGenerationDuration(Number(event.currentTarget.value) / H3_FRAMES_PER_SECOND)}
                                    aria-describedby="generation-duration-help"
                                >
                                    <For each={H3_DURATION_OPTIONS}>
                                        {(option) => <option value={String(option.frames)}>{formatH3Duration(option)}</option>}
                                    </For>
                                </select>
                                <p id="generation-duration-help" class="field-help">{durationHelp()}</p>
                            </div>
                            <div class="field-group">
                                <label class="field-label" for="generation-resolution">Разрешение</label>
                                <select
                                    id="generation-resolution"
                                    value={props.generationResolution}
                                    onChange={(event) => props.onGenerationResolution(event.currentTarget.value === 'draft' ? 'draft' : 'standard')}
                                    aria-describedby="generation-resolution-help"
                                >
                                    <For each={H3_RESOLUTION_OPTIONS}>
                                        {(option) => <option value={option.id}>{formatH3Resolution(option)}</option>}
                                    </For>
                                </select>
                                <p id="generation-resolution-help" class="field-help">Черновик в 4,8 раза меньше пикселей: быстрее и легче по памяти, удобен для проверки промта и референсов. Шаги и LoRA не меняются.</p>
                            </div>
                            <div class="field-group">
                                <span class="field-label">Режим генерации</span>
                                <div class="toggle-card">
                                    <span class="toggle-card__copy">
                                        <span class="toggle-card__title">{GENERATION_MODE_TITLES[props.generationVariant]}</span>
                                        <span class="field-help">{GENERATION_MODE_NOTES[props.generationVariant]}</span>
                                    </span>
                                </div>
                                <Show when={props.generationVariant === 'pika'}>
                                    <label class="field-label" for="ref-image-size">Размер референс-изображений</label>
                                    <select id="ref-image-size" value={props.refImageSize} onChange={(event) => props.onRefImageSize(event.currentTarget.value as 'match' | 'max')}>
                                        <option value="match">match — по площади кадра (быстрее)</option>
                                        <option value="max">max — 2048 px, лучшая идентичность (может быть в разы медленнее)</option>
                                    </select>
                                    <p class="field-help">Изображения задают внешний вид, видео — только движение. Назначь роли явно: «{'<Picture 1>'} — внешний вид персонажа, {'<Video 1>'} — только движение». Лимит промта Pika: 8 000 символов.</p>
                                </Show>
                            </div>
                        </fieldset>

                        <div class="action-row">
                            <div>
                                <p class="action-row__note">{props.realConnection ? 'Результат: MP4 с видео и аудио H3 Ref2V.' : 'Результат будет локальным demo WebM.'}</p>
                                <p class="action-row__subnote">{props.realConnection ? 'Все выбранные референсы передаются в workflow.' : 'H3 и GPU не запускаются.'}</p>
                            </div>
                            <button
                                class={buttonStyles({ tone: 'primary' })}
                                type="button"
                                disabled={!generationCanRun()}
                                onClick={() => props.onStartGeneration()}
                                aria-describedby="generation-action-help"
                            >
                                {props.isGenerationRunning ? props.realConnection ? 'Генерирую…' : 'Готовлю demo…' : 'Сгенерировать видео'}
                            </button>
                        </div>
                        <p id="generation-action-help" class="sr-only">
                            Добавь промт, чтобы запустить демонстрационную генерацию.
                        </p>
                    </div>

                    <ProgressPanel realConnection={props.realConnection} mode="generation" job={props.generationJob} filename="generation-demo.webm" scale={2} onCancel={props.onCancelGeneration} />
                </div>
            </section>

            <section
                id="upscale-panel"
                class="workspace-panel"
                role="tabpanel"
                aria-labelledby={props.showModeTabs ? 'tab-upscale' : 'studio-heading'}
                tabIndex={0}
                hidden={props.activeTab !== 'upscale'}
            >
                <div class="workspace-grid workspace-grid--upscale">
                    <div class="input-column">
                        <div class="section-title-row">
                            <div>
                                <h2>Исходное видео</h2>
                                <p>Выбери файл и коэффициент увеличения.</p>
                            </div>
                            <span class={statusStyles({ tone: 'active' })}>{props.realConnection ? 'GPU' : 'Mock'}</span>
                        </div>

                        <div class="field-group">
                            <UploadSurface
                                id="upscale-video"
                                title={props.showInterpolationOptions ? 'Видео для повышения FPS' : 'Видео для upscale'}
                                description="Одно видео · MP4, WebM или MOV"
                                acceptedFormats="ВИДЕО · 1 ФАЙЛ"
                                accept="video/mp4,video/webm,video/quicktime,.m4v"
                                disabled={props.isUpscaleRunning}
                                error={props.upscaleVideoError}
                                onFiles={props.onUpscaleVideo}
                            />
                            <Show when={props.upscaleVideo} keyed>
                                {(file) => (
                                    <SelectedVideo
                                        file={file}
                                        disabled={props.isUpscaleRunning}
                                        onRemove={props.onRemoveUpscaleVideo}
                                    />
                                )}
                            </Show>
                        </div>

                        <Show when={props.showSeedVrOptions}>
                            <div class="field-group">
                                <label class="field-label" for="seedvr-model">Модель</label>
                                <select id="seedvr-model" value={props.seedVrModel} disabled={props.isUpscaleRunning} onChange={(event) => props.onSeedVrModel(event.currentTarget.value as SeedVrModel)}>
                                    <For each={SEEDVR_MODEL_OPTIONS}>{(option) => <option value={option.id}>{option.label}</option>}</For>
                                </select>
                            </div>

                            <div class="field-group">
                                <label class="field-label" for="seedvr-batch">Batch size (кадров за проход)</label>
                                <select id="seedvr-batch" value={props.seedVrBatch} disabled={props.isUpscaleRunning} onChange={(event) => props.onSeedVrBatch(Number(event.currentTarget.value))}>
                                    <For each={SEEDVR_BATCH_SIZES}>{(size) => <option value={size}>{size === 1 ? '1 (меньше всего памяти, возможно мерцание)' : `${size} (быстрее, стабильнее, больше памяти)`}</option>}</For>
                                </select>
                                <label class="field-label" for="seedvr-overlap">Temporal overlap</label>
                                <select id="seedvr-overlap" value={props.seedVrOverlap} disabled={props.isUpscaleRunning || props.seedVrBatch === 1} onChange={(event) => props.onSeedVrOverlap(Number(event.currentTarget.value))}>
                                    <For each={SEEDVR_OVERLAPS}>{(value) => <option value={value}>{value}</option>}</For>
                                </select>
                                <p class="field-help">Batch size вида 4n+1. При ошибке памяти уменьши значение. Overlap сглаживает границы между пачками и должен быть меньше batch size.</p>
                            </div>
                        </Show>

                        <Show when={props.showInterpolationOptions}>
                            <div class="field-group">
                                <label class="field-label" for="interpolation-model">Модель</label>
                                <select id="interpolation-model" value={props.interpolationModel} disabled={props.isUpscaleRunning} onChange={(event) => props.onInterpolationModel(event.currentTarget.value as InterpolationModel)}>
                                    <For each={INTERPOLATION_MODEL_OPTIONS}>{(option) => <option value={option.id}>{option.label}</option>}</For>
                                </select>
                                <label class="field-label" for="interpolation-multiplier">Во сколько раз больше кадров</label>
                                <select id="interpolation-multiplier" value={String(props.interpolationMultiplier)} disabled={props.isUpscaleRunning} onChange={(event) => props.onInterpolationMultiplier(event.currentTarget.value === '3' ? 3 : 2)}>
                                    <For each={INTERPOLATION_MULTIPLIERS}>{(value) => <option value={String(value)}>{`×${value} (FPS видео × ${value})`}</option>}</For>
                                </select>
                                <p class="field-help">Длительность и звук сохраняются, FPS растёт: например 24 → {24 * props.interpolationMultiplier}. Узел держит все кадры в памяти, для очень длинных или больших видео может не хватить памяти.</p>
                            </div>
                        </Show>

                        <Show when={!props.fixedScale && !props.showInterpolationOptions} fallback={props.fixedScale ? <p class="field-help">Масштаб этой модели фиксирован: ×{props.upscaleFactor}.</p> : null}>
                            <fieldset class="scale-fieldset" disabled={props.isUpscaleRunning}>
                                <legend class="field-label">Коэффициент</legend>
                                <div class="scale-options" role="radiogroup" aria-label="Коэффициент увеличения">
                                    <ScaleOption factor={2} selected={props.upscaleFactor === 2} onSelect={props.onUpscaleFactor} />
                                    <ScaleOption factor={4} selected={props.upscaleFactor === 4} onSelect={props.onUpscaleFactor} />
                                </div>
                                <p class="field-help">{props.realConnection ? `${props.upscaleLabel} увеличит разрешение входа, сохраняя FPS и аудио.` : 'В mock-результате будет создано короткое demo-видео с выбранной меткой.'}</p>
                            </fieldset>
                        </Show>

                        <div class="action-row">
                            <div>
                                <p class="action-row__note">{props.realConnection ? `Результат создаётся ${props.upscaleLabel}.` : 'Результат создан в браузере.'}</p>
                                <p class="action-row__subnote">{props.realConnection ? 'Обработка на GPU инстанса Vast.' : 'AI upscale и Vast не подключены.'}</p>
                            </div>
                            <button
                                class={buttonStyles({ tone: 'primary' })}
                                type="button"
                                disabled={!upscaleCanRun()}
                                onClick={() => props.onStartUpscale()}
                                aria-describedby="upscale-action-help"
                            >
                                {props.isUpscaleRunning ? props.realConnection ? 'Обрабатываю…' : 'Готовлю demo…' : props.runLabel === '' ? `Запустить upscale ×${props.upscaleFactor}` : props.runLabel}
                            </button>
                        </div>
                        <p id="upscale-action-help" class="sr-only">
                            Выбери видео, чтобы запустить демонстрационный upscale.
                        </p>
                    </div>

                    <ProgressPanel
                        realConnection={props.realConnection}
                        mode="upscale"
                        job={props.upscaleJob}
                        filename={`upscale-x${props.upscaleFactor}-demo.webm`}
                        scale={props.upscaleFactor}
                        onCancel={props.onCancelUpscale}
                    />
                </div>
            </section>
        </section>
    )
}

function UploadSurface(props: UploadSurfaceProps) {
    let fileInput: HTMLInputElement | undefined
    const [dragActive, setDragActive] = createSignal(false)

    const handleFiles = (files: FileList | File[]) => {
        props.onFiles(Array.from(files))
    }

    const handleDrop: JSX.EventHandlerUnion<HTMLDivElement, DragEvent> = (event) => {
        event.preventDefault()
        setDragActive(false)
        const dataTransfer = event.dataTransfer
        if (!props.disabled && dataTransfer !== null) {
            handleFiles(dataTransfer.files)
        }
    }

    return (
        <div
            class={`upload-surface ${dragActive() ? 'upload-surface--dragging' : ''} ${props.disabled ? 'upload-surface--disabled' : ''}`}
            onDragOver={(event) => {
                event.preventDefault()
                if (!props.disabled) {
                    setDragActive(true)
                }
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={handleDrop}
        >
            <div class="upload-surface__copy">
                <p class="upload-surface__title">{props.title}</p>
                <p class="upload-surface__hint">{props.description}</p>
            </div>
            <span class="upload-surface__format">{props.acceptedFormats}</span>
            <button
                class={buttonStyles({ tone: 'secondary', size: 'small' })}
                type="button"
                disabled={props.disabled}
                onClick={() => fileInput?.click()}
                aria-label={`Выбрать файл: ${props.title}`}
            >
                Выбрать файл
            </button>
            <input
                ref={(element) => { fileInput = element }}
                id={props.id}
                class="sr-only"
                type="file"
                accept={props.accept}
                multiple={props.multiple ?? false}
                disabled={props.disabled}
                aria-describedby={props.error === null ? `${props.id}-help` : `${props.id}-error`}
                onChange={(event) => {
                    handleFiles(event.currentTarget.files ?? [])
                    event.currentTarget.value = ''
                }}
            />
            <p id={`${props.id}-help`} class="sr-only">{props.description}</p>
            <Show when={props.error}>
                {(message) => <p id={`${props.id}-error`} class="field-error" role="alert">{message()}</p>}
            </Show>
        </div>
    )
}

function SelectedVideo(props: SelectedVideoProps) {
    const file = untrack(() => props.file)
    const previewUrl = URL.createObjectURL(file)
    onCleanup(() => URL.revokeObjectURL(previewUrl))

    return (
        <div class="selected-video">
            <video class="selected-video__preview" src={previewUrl} controls preload="metadata" aria-label={`Предпросмотр ${file.name}`} />
            <div class="selected-video__info">
                <div class="min-w-0">
                    <p class="selected-video__name" title={file.name}>{file.name}</p>
                    <p class="field-help">{formatBytes(file.size)} · локальный файл</p>
                </div>
                <button
                    class={buttonStyles({ tone: 'quiet', size: 'small' })}
                    type="button"
                    disabled={props.disabled}
                    onClick={() => props.onRemove()}
                    aria-label={`Удалить ${file.name}`}
                >
                    Убрать
                </button>
            </div>
        </div>
    )
}

function SelectedAudio(props: SelectedAudioProps) {
    const file = untrack(() => props.file)
    const previewUrl = URL.createObjectURL(file)
    onCleanup(() => URL.revokeObjectURL(previewUrl))

    return (
        <div class="selected-video">
            <audio class="selected-video__preview" src={previewUrl} controls preload="metadata" aria-label={`Предпросмотр ${file.name}`} />
            <div class="selected-video__info">
                <div class="min-w-0">
                    <p class="selected-video__name" title={file.name}>{`<Audio ${props.index}> · ${file.name}`}</p>
                    <p class="field-help">{formatBytes(file.size)} · локальный файл</p>
                </div>
                <button
                    class={buttonStyles({ tone: 'quiet', size: 'small' })}
                    type="button"
                    disabled={props.disabled}
                    onClick={() => props.onRemove()}
                    aria-label={`Удалить аудио ${file.name}`}
                >
                    Убрать
                </button>
            </div>
        </div>
    )
}

function ImageReference(props: ImageReferenceProps) {
    const file = untrack(() => props.file)
    const previewUrl = URL.createObjectURL(file)
    onCleanup(() => URL.revokeObjectURL(previewUrl))

    return (
        <figure class="contact-sheet__item">
            <img src={previewUrl} alt={`Референс ${props.index}: ${file.name}`} loading="lazy" />
            <figcaption>
                <span>{String(props.index).padStart(2, '0')}</span>
                <button
                    type="button"
                    class="contact-sheet__remove"
                    disabled={props.disabled}
                    onClick={() => props.onRemove()}
                    aria-label={`Удалить изображение ${file.name}`}
                >
                    Убрать
                </button>
            </figcaption>
        </figure>
    )
}

function ScaleOption(props: { factor: ScaleFactor; selected: boolean; onSelect: (factor: ScaleFactor) => void }) {
    return (
        <label class={`scale-option ${props.selected ? 'scale-option--selected' : ''}`}>
            <input
                type="radio"
                name="upscale-factor"
                value={props.factor}
                checked={props.selected}
                onChange={() => props.onSelect(props.factor)}
            />
            <span class="scale-option__value">×{props.factor}</span>
            <span class="scale-option__caption">{props.factor} раза</span>
        </label>
    )
}

function ProgressPanel(props: ProgressPanelProps) {
    const job = () => props.job
    const event = () => job().progress
    const stages = () => MOCK_STAGES[props.mode]
    const resultBlob = () => job().status === 'complete' ? job().result : null

    return (
        <Show when={props.realConnection} fallback={
            <aside class="result-column" aria-label={props.mode === 'generation' ? 'Прогресс генерации' : 'Прогресс upscale'}>
                <div class="result-column__heading">
                    <div>
                        <h2>Результат</h2>
                        <p>{props.mode === 'generation' ? 'Появится после mock-запуска.' : `Выбран режим ×${props.scale}.`}</p>
                    </div>
                    <span class={statusStyles({ tone: job().status === 'complete' ? 'ready' : job().status === 'running' ? 'active' : job().status === 'error' || job().status === 'cancelled' ? 'error' : 'waiting' })}>
                        {job().status === 'complete' ? 'Demo готово' : job().status === 'running' ? 'Выполняется' : job().status === 'error' ? 'Ошибка' : job().status === 'cancelled' ? 'Отменено' : 'Ожидает'}
                    </span>
                </div>

                <Show when={resultBlob()} keyed fallback={<PreviewPlaceholder job={job()} mode={props.mode} />}>
                    {(blob) => <OutputPreview blob={blob} filename={props.filename} />}
                </Show>

                <Show when={job().status !== 'idle'}>
                    <div class="job-progress" aria-label="Этапы mock-обработки">
                        <div class="job-progress__summary">
                            <div>
                                <p class="meta-label">Общий прогресс</p>
                                <p class="job-progress__percent">{event()?.overallProgress ?? 0}%</p>
                            </div>
                            <p class="job-progress__current" aria-live="polite">
                                {job().status === 'complete'
                                    ? 'Демо-файл готов к просмотру и скачиванию.'
                                    : event()?.stageLabel ?? 'Запускаю mock-процесс'}
                            </p>
                        </div>
                        <ProgressBar label="Общий прогресс mock-задачи" value={event()?.overallProgress ?? 0} />
                        <ol class="stage-list">
                            <For each={stages()}>
                                {(stage, index) => {
                                    const complete = () => event()?.completedStageIds.includes(stage.id) ?? false
                                    const active = () => job().status === 'running' && event()?.stageId === stage.id
                                    const label = () => complete()
                                        ? 'готово'
                                        : active()
                                            ? `${event()?.stageProgress ?? 0}%`
                                            : 'ожидает'

                                    return (
                                        <li class={`stage-row ${active() ? 'stage-row--active' : ''}`}>
                                            <span class={`stage-row__number ${complete() ? 'stage-row__number--done' : ''}`} aria-hidden="true">
                                                {complete() ? '✓' : String(index() + 1).padStart(2, '0')}
                                            </span>
                                            <span class="stage-row__label">{stage.label}</span>
                                            <span class="stage-row__status">{label()}</span>
                                        </li>
                                    )
                                }}
                            </For>
                        </ol>
                        <JobTimer job={job()} />
                        <CancelButton job={job()} onCancel={props.onCancel} />
                        <Show when={job().error}>
                            {(message) => <p class="field-error" role="alert">{message()}</p>}
                        </Show>
                    </div>
                    <p class="demo-disclaimer">Прогресс демонстрационный. Внешние модели и сервисы не вызываются.</p>
                </Show>
            </aside>
        }>
            <RealProgressPanel job={props.job} mode={props.mode} onCancel={props.onCancel} />
        </Show>
    )
}

function PreviewPlaceholder(props: { job: JobState; mode: OperationMode }) {
    return (
        <div class="preview-placeholder" aria-label="Ожидание demo-результата">
            <div class="preview-placeholder__frame">
                <div class="preview-placeholder__crop preview-placeholder__crop--top-left" aria-hidden="true" />
                <div class="preview-placeholder__crop preview-placeholder__crop--top-right" aria-hidden="true" />
                <div class="preview-placeholder__crop preview-placeholder__crop--bottom-left" aria-hidden="true" />
                <div class="preview-placeholder__crop preview-placeholder__crop--bottom-right" aria-hidden="true" />
                <span class="preview-placeholder__crosshair" aria-hidden="true">+</span>
                <span class="preview-placeholder__frame-label">{props.mode === 'generation' ? '16:9 · preview' : 'video · preview'}</span>
                <Show when={props.job.status === 'running'}>
                    <div class="preview-placeholder__scanline" aria-hidden="true" />
                </Show>
                <p>{props.job.status === 'running' ? 'Собираю demo-кадр' : 'Здесь появится mock-видео'}</p>
                <span class="preview-placeholder__subline">локальная обработка · без GPU</span>
            </div>
            <div class="preview-placeholder__footer">
                <span>Предпросмотр</span>
                <span>WEBM · DEMO</span>
            </div>
        </div>
    )
}

function OutputPreview(props: { blob: Blob; filename: string }) {
    const blob = untrack(() => props.blob)
    const filename = untrack(() => props.filename)
    const url = URL.createObjectURL(blob)
    onCleanup(() => URL.revokeObjectURL(url))

    return (
        <div class="output-preview">
            <video class="output-preview__video" src={url} controls playsinline preload="metadata" aria-label="Локальное demo-видео" />
            <div class="output-preview__meta">
                <div class="min-w-0">
                    <p class="output-preview__filename" title={filename}>{filename}</p>
                    <p class="field-help">{formatBytes(blob.size)} · demo, не результат модели</p>
                </div>
                <a class={buttonStyles({ tone: 'primary', size: 'small' })} href={url} download={filename}>
                    Скачать demo
                </a>
            </div>
        </div>
    )
}

function isAbortError(error: unknown): boolean {
    return error instanceof Error && error.name === 'AbortError'
}

function formatDuration(totalMilliseconds: number): string {
    const totalSeconds = Math.floor(Math.max(totalMilliseconds, 0) / 1000)
    const hours = Math.floor(totalSeconds / 3600)
    const minutes = Math.floor((totalSeconds % 3600) / 60)
    const seconds = totalSeconds % 60
    const mm = String(minutes).padStart(2, '0')
    const ss = String(seconds).padStart(2, '0')
    return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`
}

function JobTimer(props: { job: JobState }) {
    const [now, setNow] = createSignal(Date.now())
    createEffect(() => {
        if (props.job.status === 'running') {
            setNow(Date.now())
            const timer = window.setInterval(() => setNow(Date.now()), 500)
            onCleanup(() => window.clearInterval(timer))
        }
    })
    const elapsedMs = () => {
        const started = props.job.startedAtMs
        if (started === null) {
            return null
        }
        return (props.job.finishedAtMs ?? now()) - started
    }
    const serverSeconds = () => props.job.realProgress?.elapsedSeconds ?? null

    return (
        <Show when={elapsedMs()}>
            {(elapsed) => (
                <div class="job-timer" data-testid="job-timer">
                    <p class="meta-label">
                        {props.job.status === 'running' ? 'Идёт' : 'Заняло'}: <strong>{formatDuration(elapsed())}</strong>
                        {props.job.status === 'running' ? '' : ' от запуска до финала'}
                    </p>
                    <Show when={serverSeconds()}>
                        {(seconds) => <p class="field-help">Выполнение на GPU: {formatDuration(seconds() * 1000)}</p>}
                    </Show>
                </div>
            )}
        </Show>
    )
}

function CancelButton(props: { job: JobState; onCancel: () => void }) {
    return (
        <Show when={props.job.status === 'running'}>
            <button
                class={buttonStyles({ tone: 'secondary', size: 'small' })}
                type="button"
                disabled={props.job.cancelling}
                onClick={() => props.onCancel()}
            >
                {props.job.cancelling ? 'Отменяю…' : 'Отменить'}
            </button>
        </Show>
    )
}

function RealProgressPanel(props: { job: JobState; mode: OperationMode; onCancel: () => void }) {
    const progress = () => props.job.realProgress
    const resultUrl = () => props.job.status === 'complete' ? progress()?.resultUrl : null
    return (
        <aside class="result-column" aria-label={props.mode === 'generation' ? 'Прогресс генерации' : 'Прогресс upscale'}>
            <div class="result-column__heading">
                <div><h2>Результат</h2><p>Реальная ComfyUI job на Vast</p></div>
                <span class={statusStyles({ tone: props.job.status === 'complete' ? 'ready' : props.job.status === 'error' || props.job.status === 'cancelled' ? 'error' : 'active' })}>
                    {props.job.status === 'complete' ? 'Видео готово' : props.job.status === 'error' ? 'Ошибка' : props.job.status === 'cancelled' ? 'Отменено' : props.job.status === 'running' ? 'Выполняется' : 'Ожидает'}
                </span>
            </div>
            <Show when={resultUrl()} keyed fallback={<div class="preview-placeholder"><div class="preview-placeholder__frame"><p>Здесь появится результат GPU</p><span class="preview-placeholder__subline">H3 Ref2V / SeedVR2 · MP4</span></div></div>}>
                {(url) => <div class="output-preview">
                    <video class="output-preview__video" src={url} controls playsinline preload="metadata" aria-label="Результат ComfyUI" />
                    <div class="output-preview__meta">
                        <div class="min-w-0"><p class="output-preview__filename">{progress()?.filename}</p><p class="field-help">Результат модели на Vast</p></div>
                        <a class={buttonStyles({ tone: 'primary', size: 'small' })} href={url} download={progress()?.filename}>Скачать видео</a>
                    </div>
                </div>}
            </Show>
            <Show when={props.job.status !== 'idle'}>
                <div class="job-progress">
                    <p class="job-progress__current" aria-live="polite">{progress()?.currentMessage ?? 'Проверяю входы и передаю workflow в ComfyUI…'}</p>
                    <Show when={progress()?.progressPercent !== null && progress()?.progressPercent !== undefined}>
                        <p class="meta-label">{progress()?.progressLabel} · {progress()?.progressPercent}%</p>
                        <ProgressBar label="Фактический прогресс текущей операции" value={progress()?.progressPercent ?? 0} />
                    </Show>
                    <JobTimer job={props.job} />
                    <CancelButton job={props.job} onCancel={props.onCancel} />
                    <Show when={props.job.error}>{(error) => <p class="field-error" role="alert">{error()}</p>}</Show>
                </div>
                <p class="demo-disclaimer">Процент относится к текущей передаче или узлу, не к общей длительности. Без измерений показывается только статус.</p>
            </Show>
        </aside>
    )
}

export default App
