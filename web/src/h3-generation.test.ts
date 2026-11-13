import { describe, expect, it } from 'vitest'
import {
    formatH3Duration,
    H3_DURATION_MAX_SECONDS,
    H3_DURATION_MIN_SECONDS,
    H3_DURATION_OPTIONS,
    h3FrameCountForDuration,
    h3GeneratedDurationSeconds,
    areGuideFramesValid,
    guideFrameError,
    MAX_GUIDE_FRAMES,
} from './h3-generation'
import type { GuideFrame } from './h3-generation'

const guide = (id: number, frame: number): GuideFrame => ({ id, frame, file: new File([''], `guide-${id}.png`, { type: 'image/png' }) })

describe('guide frames', () => {
    it('allows up to four guides, mirroring the backend limit', () => {
        expect(MAX_GUIDE_FRAMES).toBe(4)
    })

    it('accepts distinct integer frames inside the clip', () => {
        const guides = [guide(1, 0), guide(2, 60), guide(3, 123)]
        expect(areGuideFramesValid(guides, 124)).toBe(true)
        expect(areGuideFramesValid([], 124)).toBe(true)
    })

    it('rejects negative, fractional, out-of-clip and duplicate frames', () => {
        expect(guideFrameError([guide(1, -1)], 0, 124)).not.toBeNull()
        expect(guideFrameError([guide(1, 1.5)], 0, 124)).not.toBeNull()
        expect(guideFrameError([guide(1, Number.NaN)], 0, 124)).not.toBeNull()
        expect(guideFrameError([guide(1, 124)], 0, 124)).not.toBeNull()
        const duplicates = [guide(1, 5), guide(2, 5)]
        expect(guideFrameError(duplicates, 0, 124)).not.toBeNull()
        expect(guideFrameError(duplicates, 1, 124)).not.toBeNull()
        expect(areGuideFramesValid(duplicates, 124)).toBe(false)
    })
})

describe('H3 generation duration', () => {
    it('snaps requested fractional seconds up to the native frame grid', () => {
        expect(h3FrameCountForDuration(H3_DURATION_MIN_SECONDS)).toBe(124)
        expect(h3FrameCountForDuration(5.5)).toBe(141)
        expect(h3FrameCountForDuration(7)).toBe(175)
        expect(h3FrameCountForDuration(H3_DURATION_MAX_SECONDS)).toBe(362)
        expect(h3GeneratedDurationSeconds(5)).toBeCloseTo(124 / 24)
    })

    it('offers exactly the 15 native-grid durations, each mapping to itself', () => {
        expect(H3_DURATION_OPTIONS.map((option) => option.frames)).toEqual(
            [124, 141, 158, 175, 192, 209, 226, 243, 260, 277, 294, 311, 328, 345, 362],
        )
        for (const option of H3_DURATION_OPTIONS) {
            expect(h3FrameCountForDuration(option.seconds)).toBe(option.frames)
        }
        expect(H3_DURATION_OPTIONS.map(formatH3Duration)).toContain('13.667 с · 328 кадр.')
        expect(H3_DURATION_OPTIONS.map(formatH3Duration)).toContain('12.250 с · 294 кадр.')
        expect(H3_DURATION_OPTIONS.map(formatH3Duration)).toContain('14.375 с · 345 кадр.')
    })

    it('rejects non-finite and out-of-range durations', () => {
        for (const duration of [Number.NaN, Number.POSITIVE_INFINITY, 4.99, 15.1]) {
            expect(h3FrameCountForDuration(duration)).toBeNull()
            expect(h3GeneratedDurationSeconds(duration)).toBeNull()
        }
    })
})
