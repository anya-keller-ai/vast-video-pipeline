import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import modelManifest from '../../config/models.lock.json' with { type: 'json' }
import { makePlayableWebM, PNG_FIXTURE } from './fixtures'

async function mockRealApi(page: Page): Promise<unknown[]> {
    const video = await makePlayableWebM(page, 2_100)
    const requests: unknown[] = []
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    page.on('console', (message) => {
        if (message.type() === 'error') {
            errors.push(message.text())
        }
    })
    let jobCount = 0
    let pollCount = 0
    const assetsById = new Map(modelManifest.models.map((asset) => [asset.id, asset]))
    const bundleSnapshot = {
        bundles: modelManifest.bundles.map((bundle) => {
            const assets = bundle.assets.flatMap((assetId) => {
                const asset = assetsById.get(assetId)
                if (asset === undefined) return []
                const shared = modelManifest.bundles.some((other) => other.id !== bundle.id
                    && other.assets.some((otherAssetId) => assetsById.get(otherAssetId)?.target === asset.target))
                return [{ ...asset, format: asset.format ?? 'unknown', status: 'ready', progress: 100,
                    downloadedBytes: asset.bytes, storageBytes: asset.bytes, error: null, shared }]
            })
            const totalBytes = assets.reduce((total, asset) => total + asset.bytes, 0)
            return {
                ...bundle, enabled: bundle.enabled ?? true, blockedReason: bundle.blocked_reason ?? null,
                executionEnabled: bundle.execution_enabled ?? true,
                executionBlockedReason: bundle.execution_blocked_reason ?? null,
                phase: 'ready', assets, totalBytes, readyBytes: totalBytes, pendingBytes: 0,
                downloadProgress: 100, freeDiskBytes: 1_000_000_000_000, diskRequiredBytes: 0,
                diskSufficient: true, canDownload: false, canDelete: true, deleteBlockedReason: null,
                currentMessage: null, error: null,
            }
        }),
        activeDownloadBundleId: null,
        freeDiskBytes: 1_000_000_000_000,
        minimumFreeBytes: 20 * 1024 ** 3,
    }
    const currentJob = () => ({
        id: String(jobCount).padStart(32, '0'), status: 'queued', currentMessage: 'В очереди ComfyUI.',
        progressPercent: null, progressLabel: null, resultUrl: null, filename: 'mocked-model-result.webm', error: null,
    })
    await page.route('**/api/**', async (route) => {
        const path = new URL(route.request().url()).pathname
        if (path === '/api/config') {
            await route.fulfill({ json: { realConnection: true } })
        } else if (path === '/api/model-bundles') {
            await route.fulfill({ json: bundleSnapshot })
        } else if (path.startsWith('/api/uploads/')) {
            await route.fulfill({ status: 201, json: { id: path.split('/').at(-1) } })
        } else if (path === '/api/jobs') {
            requests.push(route.request().postDataJSON() as unknown)
            jobCount += 1
            pollCount = 0
            await route.fulfill({ status: 202, json: currentJob() })
        } else if (path.endsWith('/output')) {
            await route.fulfill({ contentType: 'video/webm', body: video })
        } else if (path.startsWith('/api/jobs/')) {
            pollCount += 1
            await route.fulfill({ json: pollCount === 1 ? {
                ...currentJob(), status: 'running', currentMessage: 'GPU: measured node progress.',
                progressPercent: 56, progressLabel: 'Узел 1/2: Sampler',
            } : {
                ...currentJob(), status: 'complete', currentMessage: 'Результат сохранён.', progressPercent: 100,
                resultUrl: `/api/jobs/${currentJob().id}/output`,
            } })
        } else {
            await route.abort()
        }
    })
    await page.goto('/')
    await expect(page.getByText('Vast · реальные модели и GPU', { exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'MiniMax H3', exact: true })).toBeVisible()
    expect(errors).toEqual([])
    return requests
}

async function expectRealOutput(page: Page, mode: 'generation' | 'upscale'): Promise<void> {
    await expect(page.getByLabel('Результат ComfyUI')).toBeVisible()
    await expect.poll(() => page.evaluate<number>('document.querySelector(".output-preview__video")?.readyState ?? 0')).toBeGreaterThanOrEqual(1)
    const progressLabel = mode === 'generation' ? 'Прогресс генерации' : 'Прогресс upscale'
    await expect(page.getByRole('complementary', { name: progressLabel }).getByText('Реальная ComfyUI job на Vast', { exact: true })).toBeVisible()
    const download = page.waitForEvent('download')
    await page.getByRole('link', { name: 'Скачать видео' }).click()
    expect((await download).suggestedFilename()).toBe('mocked-model-result.webm')
}

test('shows current model download speed and ETA from recent bundle snapshots', async ({ page }) => {
    const bundleId = 'h3_base'
    const bundleSpec = modelManifest.bundles.find((bundle) => bundle.id === bundleId)
    if (bundleSpec === undefined) {
        throw new Error(`Missing test bundle ${bundleId}`)
    }
    const assetsById = new Map(modelManifest.models.map((asset) => [asset.id, asset]))
    const totalBytes = bundleSpec.assets.reduce((total, assetId) => total + (assetsById.get(assetId)?.bytes ?? 0), 0)
    const mebibyte = 1024 ** 2
    let pollCount = 0

    await page.route('**/api/**', async (route) => {
        const path = new URL(route.request().url()).pathname
        if (path === '/api/config') {
            await route.fulfill({ json: { realConnection: true } })
            return
        }
        if (path !== '/api/model-bundles') {
            await route.abort()
            return
        }

        const transferredBytes = Math.min(pollCount * mebibyte, totalBytes - 1)
        pollCount += 1
        const freeDiskBytes = 1_000_000_000_000
        const bundles = modelManifest.bundles.map((bundle) => {
            const isActive = bundle.id === bundleId
            const assets = bundle.assets.flatMap((assetId, index) => {
                const asset = assetsById.get(assetId)
                if (asset === undefined) return []
                const downloadedBytes = isActive && index === 0 ? Math.min(transferredBytes, asset.bytes) : 0
                const status = isActive && index === 0 ? 'downloading' : 'waiting'
                const shared = modelManifest.bundles.some((other) => other.id !== bundle.id
                    && other.assets.some((otherAssetId) => assetsById.get(otherAssetId)?.target === asset.target))
                return [{ ...asset, format: asset.format ?? 'unknown', status,
                    progress: Math.round(downloadedBytes / asset.bytes * 100), downloadedBytes,
                    storageBytes: downloadedBytes, error: null, shared }]
            })
            const bundleBytes = assets.reduce((total, asset) => total + asset.bytes, 0)
            const pendingBytes = isActive ? bundleBytes - transferredBytes : bundleBytes
            return {
                ...bundle, enabled: bundle.enabled ?? true, blockedReason: bundle.blocked_reason ?? null,
                executionEnabled: bundle.execution_enabled ?? true,
                executionBlockedReason: bundle.execution_blocked_reason ?? null,
                phase: isActive ? 'downloading' : 'waiting', assets, totalBytes: bundleBytes,
                readyBytes: 0, pendingBytes,
                downloadProgress: bundleBytes > 0 ? Math.round((bundleBytes - pendingBytes) / bundleBytes * 100) : 0,
                freeDiskBytes, diskRequiredBytes: pendingBytes, diskSufficient: true,
                canDownload: false, canDelete: !isActive,
                deleteBlockedReason: isActive ? 'Download active.' : null,
                currentMessage: isActive ? 'Загружаю тестовый bundle.' : null, error: null,
            }
        })
        await route.fulfill({ json: {
            bundles, activeDownloadBundleId: bundleId, freeDiskBytes,
            minimumFreeBytes: 20 * 1024 ** 3,
        } })
    })

    await page.goto('/')
    const telemetry = page.getByTestId('model-download-telemetry')
    await expect(telemetry).toBeVisible()
    await expect.poll(() => pollCount, { timeout: 10_000 }).toBeGreaterThanOrEqual(10)
    await expect(telemetry).not.toContainText('рассчитывается…')
    await expect(telemetry).toContainText(/Скорость: [\d,]+ (?:КиБ|МиБ)\/с/)
    await expect(telemetry).toContainText(/Осталось: ~\d+ (?:с|мин|ч)/)
})

test('real mode sends prompt, all nine images and video then previews server output', async ({ page }) => {
    const requests = await mockRealApi(page)
    await page.locator('#generation-images').setInputFiles(Array.from({ length: 9 }, (_, index) => ({
        name: `reference-${index + 1}.png`, mimeType: 'image/png', buffer: PNG_FIXTURE,
    })))
    const video = await makePlayableWebM(page, 2_100)
    await page.locator('#generation-video').setInputFiles({ name: 'reference.webm', mimeType: 'video/webm', buffer: video })
    await page.locator('#generation-audios').setInputFiles([
        { name: 'voice.wav', mimeType: 'audio/wav', buffer: Buffer.from('RIFF') },
        { name: 'music.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('ID3') },
    ])
    await expect(page.getByText('2 / 3', { exact: true })).toBeVisible()
    await page.getByLabel('Промт', { exact: true }).fill('Cinematic <Picture 1> and <Video 1>, preserve subjects.')
    await page.getByLabel('Длительность, секунд').selectOption('192')
    await page.locator('#generation-resolution').selectOption('draft')
    await expect(page.getByText(/H3 создаст ровно 8\.000 с/)).toBeVisible()
    await page.getByRole('button', { name: 'Сгенерировать видео', exact: true }).click()
    await expect(page.getByText('Узел 1/2: Sampler · 56%', { exact: true })).toBeVisible()
    await expectRealOutput(page, 'generation')
    expect(requests).toHaveLength(1)
    expect(requests[0]).toEqual({
        mode: 'generation', prompt: 'Cinematic <Picture 1> and <Video 1>, preserve subjects.',
        referenceImageIds: Array.from({ length: 9 }, () => expect.any(String) as unknown), referenceVideoId: expect.any(String),
        referenceAudioIds: [expect.any(String) as unknown, expect.any(String) as unknown],
        durationSeconds: 8, resolution: 'draft', turboLora: false,
    })
    await expect(page.getByText('Mock', { exact: true })).toHaveCount(0)
})

test('real mode sends SeedVR2 scale ×2 and ×4 without calling browser demo', async ({ page }) => {
    const requests = await mockRealApi(page)
    await page.getByRole('tab', { name: 'SeedVR2' }).click()
    const video = await makePlayableWebM(page)
    await page.locator('#upscale-video').setInputFiles({ name: 'source.webm', mimeType: 'video/webm', buffer: video })
    for (const scale of [2, 4] as const) {
        await page.getByRole('radio', { name: `×${scale} ${scale} раза`, exact: true }).check()
        await page.getByRole('button', { name: `Запустить upscale ×${scale}` }).click()
        await expectRealOutput(page, 'upscale')
    }
    expect(requests).toEqual([2, 4].map((scale) => ({ mode: 'upscale', videoId: expect.any(String), scale, model: '7b_sharp', batchSize: 1, temporalOverlap: 0 })))
    await expect(page.getByText('Mock', { exact: true })).toHaveCount(0)
})

test('real mode sends FlashVSR+ variant and scale without SeedVR2 fields', async ({ page }) => {
    const requests = await mockRealApi(page)
    const video = await makePlayableWebM(page)
    for (const [tab, variant] of [['FlashVSR+ Turbo', 'turbo'], ['FlashVSR+ Base', 'base']] as const) {
        await page.getByRole('tab', { name: tab }).click()
        await page.locator('#upscale-video').setInputFiles({ name: 'source.webm', mimeType: 'video/webm', buffer: video })
        await page.getByRole('radio', { name: '×4 4 раза', exact: true }).check()
        await page.getByRole('button', { name: 'Запустить upscale ×4' }).click()
        await expectRealOutput(page, 'upscale')
        expect(requests.at(-1)).toEqual({ mode: 'flashvsr', videoId: expect.any(String), scale: 4, variant })
    }
})

test('real mode sends the model key for each fixed-scale upscale tab', async ({ page }) => {
    const requests = await mockRealApi(page)
    const video = await makePlayableWebM(page)
    for (const [tab, model, scale] of [
        ['RealESRGAN x2plus', 'realesrgan_x2plus', 2],
        ['RealESRGAN AnimeVideo-v3', 'realesrgan_animevideo_v3', 4],
    ] as const) {
        await page.getByRole('tab', { name: tab }).click()
        await page.locator('#upscale-video').setInputFiles({ name: 'source.webm', mimeType: 'video/webm', buffer: video })
        await page.getByRole('button', { name: `Запустить upscale ×${scale}` }).click()
        await expectRealOutput(page, 'upscale')
        expect(requests.at(-1)).toEqual({ mode: 'upscale_model', videoId: expect.any(String), model })
    }
})

test('real mode sends audio references from the Pika tab', async ({ page }) => {
    const requests = await mockRealApi(page)
    await page.getByRole('tab', { name: 'Pika H3 Flex' }).click()
    await page.locator('#generation-images').setInputFiles({ name: 'reference.png', mimeType: 'image/png', buffer: PNG_FIXTURE })
    await page.locator('#generation-audios').setInputFiles({ name: 'voice.wav', mimeType: 'audio/wav', buffer: Buffer.from('RIFF') })
    await page.locator('#generation-resolution').selectOption('draft')
    await page.getByLabel('Промт', { exact: true }).fill('<Picture 1> speaks with <Audio 1>.')
    await page.getByRole('button', { name: 'Сгенерировать видео', exact: true }).click()
    await expectRealOutput(page, 'generation')
    expect(requests).toHaveLength(1)
    expect(requests[0]).toEqual(expect.objectContaining({ mode: 'pika', resolution: 'draft', referenceAudioIds: [expect.any(String) as unknown] }))
})

test('real mode sends the negative prompt with its strength and blocks invalid syntax', async ({ page }) => {
    const requests = await mockRealApi(page)
    await page.getByRole('tab', { name: 'Pika H3 Flex' }).click()
    await page.locator('#generation-images').setInputFiles({ name: 'reference.png', mimeType: 'image/png', buffer: PNG_FIXTURE })
    await page.getByLabel('Промт', { exact: true }).fill('<Picture 1> stands on a table.')
    const run = page.getByRole('button', { name: 'Сгенерировать видео', exact: true })
    await page.locator('#negative-prompt').fill('(red:-9)')
    await expect(page.getByRole('alert')).toContainText('скобки')
    await expect(run).toBeDisabled()
    await page.locator('#negative-prompt').fill('red dress, clothes')
    await page.locator('#negative-strength').fill('2.5')
    await expect(run).toBeEnabled()
    await run.click()
    await expectRealOutput(page, 'generation')
    expect(requests).toHaveLength(1)
    expect(requests[0]).toEqual(expect.objectContaining({ mode: 'pika', negativePrompt: 'red dress, clothes', negativeStrength: 2.5 }))
})

test('real mode sends interpolation model and multiplier', async ({ page }) => {
    const requests = await mockRealApi(page)
    const video = await makePlayableWebM(page)
    await page.getByRole('tab', { name: 'Повышение FPS' }).click()
    await page.locator('#upscale-video').setInputFiles({ name: 'source.webm', mimeType: 'video/webm', buffer: video })
    await page.locator('#interpolation-model').selectOption('film')
    await page.locator('#interpolation-multiplier').selectOption('3')
    await page.getByRole('button', { name: 'Повысить FPS ×3' }).click()
    await expectRealOutput(page, 'upscale')
    expect(requests.at(-1)).toEqual({ mode: 'interpolate', videoId: expect.any(String), model: 'film', multiplier: 3 })
})
