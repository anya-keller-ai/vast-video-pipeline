interface DemoVideoInputs {
    referenceVideo: File | null
    referenceImage: File | null
    prompt: string
}

export type DemoVideoOptions = (DemoVideoInputs & {
    mode: 'generation'
    durationSeconds: number
}) | (DemoVideoInputs & {
    mode: 'upscale'
    durationSeconds: null
    scale: 2 | 4
})

interface LoadedVideo {
    element: HTMLVideoElement
    objectUrl: string
}

const DEMO_DURATION_MS = 1_900
const RECORDING_FPS = 24
const VIDEO_MIME_TYPES = [
    'video/webm;codecs=vp9',
    'video/webm;codecs=vp8',
    'video/webm',
] as const

export async function createDemoWebM(
    options: DemoVideoOptions,
    reportProgress: (progress: number) => void,
): Promise<Blob> {
    if (typeof MediaRecorder === 'undefined' || typeof HTMLCanvasElement.prototype.captureStream !== 'function') {
        throw new Error('Браузер не поддерживает локальную запись demo WebM. Открой страницу в Chromium.')
    }

    const durationMs = options.durationSeconds === null
        ? DEMO_DURATION_MS
        : options.durationSeconds * 1_000
    const canvas = document.createElement('canvas')
    canvas.width = options.mode === 'upscale' ? 320 * options.scale : 960
    canvas.height = options.mode === 'upscale' ? 180 * options.scale : 540

    const context = canvas.getContext('2d')
    if (context === null) {
        throw new Error('Браузер не смог создать canvas для demo-видео.')
    }

    const loadedVideo = options.referenceVideo === null ? null : await loadVideo(options.referenceVideo)
    const referenceImage = await loadImage(options.referenceImage)
    const stream = canvas.captureStream(RECORDING_FPS)
    const mimeType = VIDEO_MIME_TYPES.find((candidate) => MediaRecorder.isTypeSupported(candidate))

    if (mimeType === undefined) {
        releaseMedia(stream, loadedVideo, referenceImage)
        throw new Error('Браузер не поддерживает кодирование demo WebM.')
    }

    const recorder = new MediaRecorder(stream, {
        mimeType,
        videoBitsPerSecond: 900_000,
    })

    const chunks: Blob[] = []
    const startedAt = performance.now()
    let animationFrame = 0
    let settled = false

    return new Promise((resolve, reject) => {
        const cleanup = () => {
            cancelAnimationFrame(animationFrame)
            releaseMedia(stream, loadedVideo, referenceImage)
        }

        recorder.addEventListener('dataavailable', (event: BlobEvent) => {
            if (event.data.size > 0) {
                chunks.push(event.data)
            }
        })

        recorder.addEventListener('error', () => {
            if (settled) {
                return
            }

            settled = true
            cleanup()
            reject(new Error('Браузер не смог записать demo-видео. Повтори mock-задание.'))
        })

        recorder.addEventListener('stop', () => {
            if (settled) {
                return
            }

            settled = true
            cleanup()
            const blob = new Blob(chunks, { type: mimeType })

            if (blob.size === 0) {
                reject(new Error('Браузер создал пустой demo-файл. Повтори mock-задание.'))
                return
            }

            reportProgress(1)
            resolve(blob)
        })

        recorder.start(120)
        drawFrame(context, canvas, 0, durationMs, options, loadedVideo?.element ?? null, referenceImage)

        const recordFrame = (timestamp: number) => {
            const elapsedMs = Math.min(timestamp - startedAt, durationMs)
            const progress = elapsedMs / durationMs
            drawFrame(context, canvas, elapsedMs, durationMs, options, loadedVideo?.element ?? null, referenceImage)
            reportProgress(progress)

            if (progress >= 1) {
                recorder.stop()
                return
            }

            animationFrame = requestAnimationFrame(recordFrame)
        }

        animationFrame = requestAnimationFrame(recordFrame)
    })
}

async function loadVideo(file: File): Promise<LoadedVideo | null> {
    const objectUrl = URL.createObjectURL(file)
    const element = document.createElement('video')
    element.preload = 'auto'
    element.muted = true
    element.loop = true
    element.playsInline = true
    element.src = objectUrl

    try {
        await new Promise<void>((resolve) => {
            const timeout = globalThis.setTimeout(resolve, 2_000)
            element.addEventListener('loadeddata', () => {
                globalThis.clearTimeout(timeout)
                resolve()
            }, { once: true })
            element.addEventListener('error', () => {
                globalThis.clearTimeout(timeout)
                resolve()
            }, { once: true })
            element.load()
        })

        if (element.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
            URL.revokeObjectURL(objectUrl)
            return null
        }

        await element.play().catch(() => undefined)
        return { element, objectUrl }
    } catch {
        URL.revokeObjectURL(objectUrl)
        return null
    }
}

async function loadImage(file: File | null): Promise<ImageBitmap | null> {
    if (file === null || typeof createImageBitmap !== 'function') {
        return null
    }

    try {
        return await createImageBitmap(file)
    } catch {
        return null
    }
}

function drawFrame(
    context: CanvasRenderingContext2D,
    canvas: HTMLCanvasElement,
    elapsedMs: number,
    durationMs: number,
    options: DemoVideoOptions,
    sourceVideo: HTMLVideoElement | null,
    referenceImage: ImageBitmap | null,
): void {
    context.fillStyle = '#19211f'
    context.fillRect(0, 0, canvas.width, canvas.height)

    const source = sourceVideo !== null && sourceVideo.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA
        ? sourceVideo
        : referenceImage

    if (source !== null) {
        drawCover(context, source, canvas.width, canvas.height, elapsedMs, durationMs)
    } else {
        drawDemoScene(context, canvas.width, canvas.height, elapsedMs, durationMs)
    }

    const barHeight = Math.max(54, Math.round(canvas.height * 0.17))
    context.fillStyle = 'rgba(17, 23, 21, 0.84)'
    context.fillRect(0, canvas.height - barHeight, canvas.width, barHeight)
    context.fillStyle = '#f4f5f2'
    context.font = `600 ${Math.max(13, Math.round(canvas.height * 0.035))}px "Geist Variable", sans-serif`
    context.textBaseline = 'middle'

    const mark = options.mode === 'generation'
        ? 'DEMO · H3 НЕ ЗАПУСКАЛСЯ'
        : `DEMO · UPSCALE ×${options.scale}`
    context.fillText(mark, Math.round(canvas.width * 0.035), canvas.height - barHeight * 0.68)

    if (options.mode === 'generation' && options.prompt.trim() !== '') {
        const prompt = truncate(options.prompt.trim(), Math.max(24, Math.floor(canvas.width / 13)))
        context.fillStyle = 'rgba(244, 245, 242, 0.76)'
        context.font = `400 ${Math.max(11, Math.round(canvas.height * 0.025))}px "Geist Variable", sans-serif`
        context.fillText(prompt, Math.round(canvas.width * 0.035), canvas.height - barHeight * 0.27)
    }

    const progress = Math.min(elapsedMs / durationMs, 1)
    context.fillStyle = '#b84735'
    context.fillRect(0, canvas.height - 3, canvas.width * progress, 3)
}

function drawCover(
    context: CanvasRenderingContext2D,
    source: CanvasImageSource,
    width: number,
    height: number,
    elapsedMs: number,
    durationMs: number,
): void {
    const imageSize = getImageSize(source)
    if (imageSize === null) {
        return
    }

    const zoom = 1.025 + Math.min(elapsedMs / durationMs, 1) * 0.025
    const sourceRatio = imageSize.width / imageSize.height
    const canvasRatio = width / height
    const drawWidth = sourceRatio > canvasRatio ? height * sourceRatio : width
    const drawHeight = sourceRatio > canvasRatio ? height : width / sourceRatio
    const zoomedWidth = drawWidth * zoom
    const zoomedHeight = drawHeight * zoom
    const drift = Math.sin(elapsedMs / durationMs * Math.PI) * width * 0.012
    const x = (width - zoomedWidth) / 2 + drift
    const y = (height - zoomedHeight) / 2

    context.drawImage(source, x, y, zoomedWidth, zoomedHeight)
}

function getImageSize(source: CanvasImageSource): { width: number; height: number } | null {
    if (source instanceof HTMLVideoElement) {
        return source.videoWidth > 0 && source.videoHeight > 0
            ? { width: source.videoWidth, height: source.videoHeight }
            : null
    }

    if (source instanceof ImageBitmap) {
        return { width: source.width, height: source.height }
    }

    return null
}

function drawDemoScene(
    context: CanvasRenderingContext2D,
    width: number,
    height: number,
    elapsedMs: number,
    durationMs: number,
): void {
    const progress = elapsedMs / durationMs
    const horizon = height * 0.58
    const movement = Math.sin(progress * Math.PI * 2) * width * 0.035

    context.fillStyle = '#283630'
    context.fillRect(0, 0, width, height)
    context.fillStyle = '#46574c'
    context.beginPath()
    context.moveTo(0, horizon)
    context.lineTo(width * 0.32 + movement, height * 0.34)
    context.lineTo(width * 0.68 + movement, height * 0.62)
    context.lineTo(width, height * 0.36)
    context.lineTo(width, height)
    context.lineTo(0, height)
    context.closePath()
    context.fill()

    context.fillStyle = '#a5a275'
    context.beginPath()
    context.arc(width * (0.72 - progress * 0.04), height * 0.27, height * 0.065, 0, Math.PI * 2)
    context.fill()

    context.strokeStyle = 'rgba(240, 242, 235, 0.22)'
    context.lineWidth = Math.max(1, width * 0.001)
    context.strokeRect(width * 0.04, height * 0.04, width * 0.92, height * 0.86)
}

function truncate(value: string, maxLength: number): string {
    if (value.length <= maxLength) {
        return value
    }

    return `${value.slice(0, maxLength - 1)}…`
}

function releaseMedia(
    stream: MediaStream,
    loadedVideo: LoadedVideo | null,
    referenceImage: ImageBitmap | null,
): void {
    for (const track of stream.getTracks()) {
        track.stop()
    }

    if (loadedVideo !== null) {
        loadedVideo.element.pause()
        loadedVideo.element.removeAttribute('src')
        URL.revokeObjectURL(loadedVideo.objectUrl)
    }

    referenceImage?.close()
}
