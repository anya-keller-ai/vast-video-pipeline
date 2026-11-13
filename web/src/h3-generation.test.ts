import { describe, expect, it } from 'vitest'
import {
    formatH3Duration,
    H3_DURATION_MAX_SECONDS,
    H3_DURATION_MIN_SECONDS,
    H3_DURATION_OPTIONS,
    h3FrameCountForDuration,
    h3GeneratedDurationSeconds,
} from './h3-generation'

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
