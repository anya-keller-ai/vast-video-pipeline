import { Buffer } from 'node:buffer'
import type { Page } from '@playwright/test'

export const PNG_FIXTURE = Buffer.from(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+i6E8AAAAASUVORK5CYII=',
    'base64',
)
export async function makePlayableWebM(page: Page, durationMs = 700): Promise<Buffer> {
    const encoded = await page.evaluate<string>(`(async () => {
        const canvas = document.createElement('canvas')
        canvas.width = 96
        canvas.height = 54
        const context = canvas.getContext('2d')
        if (!context || typeof MediaRecorder === 'undefined') {
            throw new Error('Canvas recording is not supported')
        }

        const stream = canvas.captureStream(12)
        const recorder = new MediaRecorder(stream, { mimeType: 'video/webm' })
        const chunks = []

        return await new Promise((resolve, reject) => {
            recorder.addEventListener('dataavailable', (event) => {
                if (event.data.size > 0) chunks.push(event.data)
            })
            recorder.addEventListener('error', () => reject(new Error('Input fixture recording failed')))
            recorder.addEventListener('stop', async () => {
                for (const track of stream.getTracks()) track.stop()
                const data = new Uint8Array(await new Blob(chunks, { type: 'video/webm' }).arrayBuffer())
                let binary = ''
                for (const byte of data) binary += String.fromCharCode(byte)
                resolve(btoa(binary))
            })

            recorder.start(100)
            let frame = 0
            const timer = setInterval(() => {
                context.fillStyle = frame % 2 === 0 ? '#ad432f' : '#315f50'
                context.fillRect(0, 0, canvas.width, canvas.height)
                frame += 1
            }, 80)
            setTimeout(() => {
                clearInterval(timer)
                recorder.stop()
            }, ${durationMs})
        })
    })()`)

    return Buffer.from(encoded, 'base64')
}

