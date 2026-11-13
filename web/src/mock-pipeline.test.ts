import { describe, expect, it } from 'vitest'
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
import type { PipelineDelay } from './mock-pipeline'

describe('mock pipeline validation', () => {
    it('accepts supported video formats and rejects incompatible files', () => {
        expect(validateVideoFile({ name: 'reference.mp4', type: 'video/mp4' })).toBeNull()
        expect(validateVideoFile({ name: 'reference.webm', type: 'video/webm' })).toBeNull()
        expect(validateVideoFile({ name: 'reference.mov', type: 'video/quicktime' })).toBeNull()
        expect(validateVideoFile({ name: 'reference.gif', type: 'image/gif' })).not.toBeNull()
        expect(validateVideoFile({ name: 'reference.mp4', type: 'image/png' })).not.toBeNull()
    })

    it('accepts up to nine supported reference images and explains rejected files', () => {
        const acceptedFile = new File([], 'reference.webp', { type: 'image/webp' })
        const extraFile = new File([], 'extra.png', { type: 'image/png' })
        const invalidFile = new File([], 'animation.gif', { type: 'image/gif' })
        const result = validateImageBatch(MAX_REFERENCE_IMAGES - 1, [acceptedFile, extraFile, invalidFile])

        expect(result.accepted).toEqual([acceptedFile])
        expect(result.rejected).toHaveLength(2)
        expect(result.rejected[0]?.reason).toContain('не больше')
        expect(result.rejected[1]?.reason).toContain('JPG, PNG и WebP')
    })

    it('accepts up to three supported audio references and explains rejected files', () => {
        const accepted = new File([], 'voice.wav', { type: 'audio/wav' })
        const extra = new File([], 'extra.mp3', { type: 'audio/mpeg' })
        const invalid = new File([], 'clip.mp4', { type: 'video/mp4' })
        const result = validateAudioBatch(MAX_REFERENCE_AUDIOS - 1, [accepted, extra, invalid])

        expect(result.accepted).toEqual([accepted])
        expect(result.rejected).toHaveLength(2)
        expect(result.rejected[0]?.reason).toContain('не больше 3')
        expect(result.rejected[1]?.reason).toContain('WAV, MP3')
    })

    it('requires non-whitespace prompt text', () => {
        expect(isPromptValid('  ')).toBe(false)
        expect(isPromptValid('Камера медленно движется к окну')).toBe(true)
    })
})

describe('mock pipeline progress', () => {
    it('reports ordered stages and returns the demo renderer output', async () => {
        const events: Array<{ stageId: string; overallProgress: number; completedStageIds: string[] }> = []
        const output = new Blob(['local demo'], { type: 'video/webm' })
        const controller = new AbortController()
        const wait: PipelineDelay = async () => undefined

        const result = await runMockPipeline(
            'generation',
            (event) => events.push(event),
            async (reportProgress) => {
                reportProgress(0.5)
                reportProgress(1)
                return output
            },
            controller.signal,
            wait,
        )

        const completed = events[events.length - 1]
        expect(result).toBe(output)
        expect(MOCK_STAGES.generation.map((stage) => stage.id)).toEqual(['validate', 'prepare', 'generate', 'render'])
        expect(completed?.overallProgress).toBe(100)
        expect(completed?.completedStageIds).toEqual(MOCK_STAGES.generation.map((stage) => stage.id))
        expect(events.every((event, index) => index === 0 || event.overallProgress >= (events[index - 1]?.overallProgress ?? 0))).toBe(true)
    })

    it('does not run the mock pipeline after cancellation', async () => {
        const controller = new AbortController()
        controller.abort()

        await expect(runMockPipeline(
            'upscale',
            () => undefined,
            async () => new Blob(['not reached']),
            controller.signal,
        )).rejects.toMatchObject({ name: 'AbortError' })
    })
})
