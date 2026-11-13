import { stat } from 'node:fs/promises'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { makePlayableWebM, PNG_FIXTURE } from './fixtures'

const pageErrors = new WeakMap<Page, string[]>()

test.beforeEach(({ page }) => {
    const errors: string[] = []
    pageErrors.set(page, errors)
    page.on('pageerror', (error) => errors.push(error.message))
    page.on('console', (message) => {
        if (message.type() === 'error') {
            errors.push(message.text())
        }
    })
})

test.afterEach(({ page }) => {
    expect(pageErrors.get(page) ?? []).toEqual([])
})

async function expectPlayableDemo(page: Page): Promise<void> {
    const video = page.getByLabel('Локальное demo-видео')
    await expect(video).toBeVisible()
    await expect.poll(() => page.evaluate<number>('document.querySelector(".output-preview__video")?.readyState ?? 0')).toBeGreaterThanOrEqual(1)
    await expect.poll(() => page.evaluate<number>('document.querySelector(".output-preview__video")?.duration ?? 0')).toBeGreaterThan(0)
}

async function openStudio(page: Page): Promise<void> {
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Мастерская моделей' })).toBeVisible()
    await page.getByRole('button', { name: 'Открыть mock-форму без моделей' }).click()
    await expect(page.getByRole('heading', { name: 'MiniMax H3', exact: true })).toBeVisible()
}

test('opens nine independent model tabs without downloading on load or switching', async ({ page }) => {
    const externalRequests: string[] = []
    const bundleRequests: string[] = []
    page.on('request', (request) => {
        if (!request.url().startsWith('http://127.0.0.1:4173/')) {
            externalRequests.push(request.url())
        }
        if (new URL(request.url()).pathname === '/api/model-bundles') {
            bundleRequests.push(request.method())
        }
    })

    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Мастерская моделей' })).toBeVisible()
    await expect(page.getByText('локальный mock · без загрузок')).toBeVisible()
    await expect(page.getByRole('tab', { name: 'MiniMax H3', exact: true })).toBeVisible()
    await expect(page.getByRole('tab', { name: 'MiniMax H3 Turbo' })).toBeVisible()
    await expect(page.getByRole('tab', { name: 'Pika H3 Flex' })).toBeVisible()
    await expect(page.getByRole('tab', { name: 'SeedVR2' })).toBeVisible()
    await expect(page.getByRole('tab', { name: 'FlashVSR+ Turbo' })).toBeVisible()
    await expect(page.getByRole('tab', { name: 'FlashVSR+ Base' })).toBeVisible()
    await expect(page.getByRole('tab', { name: 'NVIDIA Regenerate 2K' })).toHaveCount(0)
    await expect(page.getByRole('tab', { name: 'MiniMax H3 Fast' })).toHaveCount(0)
    await expect(page.getByRole('tab')).toHaveCount(9)
    await expect(page.getByRole('tab', { name: 'HY-OmniWeaving' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Загрузка доступна только в реальном режиме' })).toBeDisabled()

    const initialBundleRequestCount = bundleRequests.length
    for (const family of ['MiniMax H3 Turbo', 'Pika H3 Flex', 'SeedVR2', 'FlashVSR+ Turbo', 'FlashVSR+ Base', 'RealESRGAN x2plus', 'RealESRGAN AnimeVideo-v3', 'Повышение FPS', 'MiniMax H3']) {
        await page.getByRole('tab', { name: family, exact: true }).click()
    }
    expect(bundleRequests).toHaveLength(initialBundleRequestCount)
    expect(bundleRequests).toEqual(['GET'])

    for (const width of [320, 375, 414, 768, 1280]) {
        await page.setViewportSize({ width, height: 900 })
        await expect.poll(() => page.evaluate<number>('document.documentElement.scrollWidth')).toBeLessThanOrEqual(width)
    }
    expect(externalRequests).toEqual([])
})

test('Pika form has image and motion-video references without turbo toggle', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('tab', { name: 'Pika H3 Flex' }).click()
    await page.getByRole('button', { name: 'Открыть mock-форму без моделей' }).click()
    await expect(page.getByRole('heading', { name: 'Pika H3 Flex', exact: true })).toBeVisible()
    await expect(page.locator('#generation-video')).toHaveCount(1)
    await expect(page.locator('#turbo-lora')).toHaveCount(0)
    await expect(page.getByText('0 / 9', { exact: true })).toBeVisible()
})

test('cancels a running mock generation and keeps the elapsed time', async ({ page }) => {
    await openStudio(page)
    await page.getByLabel('Промт', { exact: true }).fill('Небольшой красный парусник плывёт по спокойному морю')
    await page.getByRole('button', { name: 'Сгенерировать видео' }).click()
    const timer = page.getByTestId('job-timer')
    await expect(timer).toContainText('Идёт')
    await page.getByRole('button', { name: 'Отменить' }).click()
    await expect(page.getByText('Отменено', { exact: true })).toBeVisible()
    await expect(timer).toContainText('Заняло')
    await expect(page.getByRole('button', { name: 'Отменить' })).toHaveCount(0)
    await expect(page.getByLabel('Локальное demo-видео')).toHaveCount(0)
    const frozen = await timer.innerText()
    await page.waitForTimeout(1200)
    expect(await timer.innerText()).toBe(frozen)
})

test('FlashVSR+ tabs have their own heading and no SeedVR2 controls', async ({ page }) => {
    await page.goto('/')
    for (const name of ['FlashVSR+ Turbo', 'FlashVSR+ Base']) {
        await page.getByRole('tab', { name }).click()
        await page.getByRole('button', { name: 'Открыть mock-форму без моделей' }).click()
        await expect(page.getByRole('heading', { name, exact: true, level: 1 })).toBeVisible()
        await expect(page.locator('#seedvr-model')).toHaveCount(0)
        await expect(page.locator('#seedvr-batch')).toHaveCount(0)
        await expect(page.getByRole('radio', { name: '×2 2 раза', exact: true })).toBeVisible()
    }
})

test('model upscale tabs show a fixed scale instead of the scale choice', async ({ page }) => {
    await page.goto('/')
    for (const [name, scale] of [['RealESRGAN x2plus', 2], ['RealESRGAN AnimeVideo-v3', 4]] as const) {
        await page.getByRole('tab', { name }).click()
        await page.getByRole('button', { name: 'Открыть mock-форму без моделей' }).click()
        await expect(page.getByRole('heading', { name, exact: true, level: 1 })).toBeVisible()
        await expect(page.getByText(`Масштаб этой модели фиксирован: ×${scale}.`)).toBeVisible()
        await expect(page.getByRole('radio', { name: '×2 2 раза', exact: true })).toHaveCount(0)
        await expect(page.locator('#seedvr-model')).toHaveCount(0)
    }
})

test('frame interpolation tab offers models and multipliers instead of a scale', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('tab', { name: 'Повышение FPS' }).click()
    await page.getByRole('button', { name: 'Открыть mock-форму без моделей' }).click()
    await expect(page.getByRole('heading', { name: 'Повышение FPS', exact: true, level: 1 })).toBeVisible()
    await expect(page.locator('#interpolation-model option')).toHaveText(['RIFE 4.26', 'RIFE 4.25', 'FILM'])
    await expect(page.locator('#interpolation-multiplier option')).toHaveCount(2)
    await expect(page.getByRole('radio', { name: '×2 2 раза', exact: true })).toHaveCount(0)
    await expect(page.locator('#seedvr-model')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Повысить FPS ×2' })).toBeDisabled()
    await page.locator('#interpolation-multiplier').selectOption('3')
    await expect(page.getByRole('button', { name: 'Повысить FPS ×3' })).toBeVisible()
})

test('creates a playable generation demo and provides a download', async ({ page }) => {
    await openStudio(page)

    await page.getByLabel('Промт', { exact: true }).fill('Небольшой красный парусник плывёт по спокойному морю')
    await page.locator('#generation-images').setInputFiles({
        name: 'reference.png',
        mimeType: 'image/png',
        buffer: PNG_FIXTURE,
    })
    await expect(page.getByRole('img', { name: /Референс 1:/ })).toBeVisible()
    await expect(page.getByText('1 / 9')).toBeVisible()

    const duration = page.getByLabel('Длительность, секунд')
    await expect(duration).toHaveValue('124')
    const resolution = page.locator('#generation-resolution')
    await expect(resolution).toHaveValue('standard')
    await expect(resolution.locator('option')).toHaveText(['Стандарт · 1344×768', 'Черновик · 608×352'])
    await expect(duration.locator('option')).toHaveCount(15)
    await expect(duration.locator('option').nth(10)).toHaveText('12.250 с · 294 кадр.')
    await expect(page.getByText('20 шагов · без Turbo LoRA')).toBeVisible()
    await expect(page.getByText(/Mock-демо будет длиться 5\.167 с/)).toBeVisible()
    await duration.selectOption('141')
    await expect(page.getByText(/Mock-демо будет длиться 5\.875 с/)).toBeVisible()
    await page.getByRole('button', { name: 'Сгенерировать видео' }).click()
    await expect(page.getByText('Демо-файл готов к просмотру и скачиванию.')).toBeVisible({ timeout: 20_000 })
    await expect(page.getByTestId('job-timer')).toContainText('Заняло')
    await expectPlayableDemo(page)
    await expect.poll(() => page.evaluate<number>('document.querySelector(".output-preview__video")?.duration ?? 0')).toBeGreaterThan(5)

    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('link', { name: 'Скачать demo' }).click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toBe('generation-demo.webm')
    const downloadedPath = await download.path()
    expect(downloadedPath).not.toBeNull()
    if (downloadedPath !== null) {
        expect((await stat(downloadedPath)).size).toBeGreaterThan(0)
    }
})

test('upscales an uploaded video in mock mode and downloads the WebM', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('tab', { name: 'SeedVR2' }).click()
    await page.getByRole('button', { name: 'Открыть mock-форму без моделей' }).click()
    await expect(page.getByRole('heading', { name: 'SeedVR2', exact: true })).toBeVisible()

    const video = await makePlayableWebM(page)
    expect(video.byteLength).toBeGreaterThan(0)
    await page.locator('#upscale-video').setInputFiles({
        name: 'source.webm',
        mimeType: 'video/webm',
        buffer: video,
    })
    const inputVideo = page.getByLabel('Предпросмотр source.webm')
    await expect(inputVideo).toBeVisible()
    await expect.poll(() => page.evaluate<number>('document.querySelector(".selected-video__preview")?.readyState ?? 0')).toBeGreaterThanOrEqual(1)
    await expect(page.getByRole('radio', { name: '×2 2 раза' })).toBeChecked()
    await page.getByRole('radio', { name: '×4 4 раза' }).check()
    await expect(page.getByRole('radio', { name: '×4 4 раза' })).toBeChecked()
    await expect(page.getByRole('button', { name: 'Запустить upscale ×4' })).toBeEnabled()

    await page.getByRole('button', { name: 'Запустить upscale ×4' }).click()
    await expect(page.getByText('Демо-файл готов к просмотру и скачиванию.')).toBeVisible({ timeout: 20_000 })
    await expectPlayableDemo(page)

    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('link', { name: 'Скачать demo' }).click()
    const download = await downloadPromise
    expect(download.suggestedFilename()).toBe('upscale-x4-demo.webm')
    const downloadedPath = await download.path()
    expect(downloadedPath).not.toBeNull()
    if (downloadedPath !== null) {
        expect((await stat(downloadedPath)).size).toBeGreaterThan(0)
    }
})
