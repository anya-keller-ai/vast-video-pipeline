import presets from '../../config/presets.json'

const activeProfile = Object.entries(presets.gpu_profiles)
    .find(([profileName]) => profileName === presets.active_gpu_profile)?.[1]

if (activeProfile === undefined) {
    throw new Error('H3 active GPU profile is missing from presets.')
}

const productionPreset = activeProfile.h3.production
const draftPreset = activeProfile.h3.draft
const generationRules = presets.rules.h3_generation_duration
const framesPerSecond = productionPreset.fps

export const H3_FRAMES_PER_SECOND = framesPerSecond

export type H3Resolution = 'standard' | 'draft'

export interface H3ResolutionOption {
    id: H3Resolution
    label: string
    width: number
    height: number
}

export const H3_RESOLUTION_OPTIONS: readonly H3ResolutionOption[] = [
    { id: 'standard', label: 'Стандарт', width: productionPreset.width, height: productionPreset.height },
    { id: 'draft', label: 'Черновик', width: draftPreset.width, height: draftPreset.height },
]

export function formatH3Resolution(option: H3ResolutionOption): string {
    return `${option.label} · ${option.width}×${option.height}`
}

export const H3_OUTPUT_WIDTH = productionPreset.width
export const H3_OUTPUT_HEIGHT = productionPreset.height
export const H3_DURATION_MIN_SECONDS = generationRules.min_seconds
// The last frame-grid step (362 frames) lasts a little over the nominal 15 s.
export const H3_DURATION_MAX_SECONDS = generationRules.max_frames / framesPerSecond
const FRAME_ROUNDING_TOLERANCE = 1e-6

export interface H3DurationOption {
    frames: number
    seconds: number
}

export const H3_DURATION_OPTIONS: readonly H3DurationOption[] = Array.from(
    { length: Math.floor((generationRules.max_frames - generationRules.min_frames) / generationRules.frame_step) + 1 },
    (_, index) => {
        const frames = generationRules.min_frames + index * generationRules.frame_step
        return { frames, seconds: frames / framesPerSecond }
    },
)

export function formatH3Duration(option: H3DurationOption): string {
    return `${option.seconds.toFixed(3)} с · ${option.frames} кадр.`
}

export function h3FrameCountForDuration(durationSeconds: number): number | null {
    if (!Number.isFinite(durationSeconds)
        || durationSeconds < H3_DURATION_MIN_SECONDS
        || durationSeconds > H3_DURATION_MAX_SECONDS) {
        return null
    }

    const requestedFrames = Math.ceil(durationSeconds * framesPerSecond - FRAME_ROUNDING_TOLERANCE)
    const increments = Math.max(
        0,
        Math.ceil((requestedFrames - generationRules.min_frames) / generationRules.frame_step),
    )
    return Math.min(
        generationRules.min_frames + increments * generationRules.frame_step,
        generationRules.max_frames,
    )
}

export function h3GeneratedDurationSeconds(durationSeconds: number): number | null {
    const frameCount = h3FrameCountForDuration(durationSeconds)
    return frameCount === null ? null : frameCount / framesPerSecond
}
