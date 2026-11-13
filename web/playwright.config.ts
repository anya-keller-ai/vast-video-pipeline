import { defineConfig } from '@playwright/test'
import { env } from 'node:process'

export default defineConfig({
    testDir: './e2e',
    fullyParallel: true,
    reporter: 'list',
    use: {
        baseURL: 'http://127.0.0.1:4173',
        browserName: 'chromium',
        headless: true,
        launchOptions: env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
            ? { executablePath: env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH }
            : undefined,
        trace: 'retain-on-failure',
    },
    webServer: {
        command: 'pnpm dev',
        url: 'http://127.0.0.1:4173',
        reuseExistingServer: !env.CI,
        timeout: 30_000,
    },
})
