import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath } from 'node:url'
import solid from 'vite-plugin-solid'
import { defineConfig } from 'vitest/config'
import modelManifest from '../config/models.lock.json' with { type: 'json' }

const projectRoot = fileURLToPath(new URL('../', import.meta.url))
const modelAssets = new Map(modelManifest.models.map((asset) => [asset.id, asset]))

function mockModelBundlesSnapshot() {
    const bundles = modelManifest.bundles.map((bundle) => {
        const assets = bundle.assets.flatMap((assetId) => {
            const asset = modelAssets.get(assetId)
            if (asset === undefined) {
                return []
            }
            const shared = modelManifest.bundles.some((other) => other.id !== bundle.id
                && other.assets.some((otherAssetId) => modelAssets.get(otherAssetId)?.target === asset.target))
            return [{
                ...asset,
                format: asset.format ?? 'unknown',
                status: 'waiting',
                progress: 0,
                downloadedBytes: 0,
                storageBytes: 0,
                error: null,
                shared,
            }]
        })
        const totalBytes = assets.reduce((total, asset) => total + asset.bytes, 0)
        return {
            ...bundle,
            enabled: 'enabled' in bundle ? bundle.enabled : true,
            blockedReason: 'blocked_reason' in bundle ? bundle.blocked_reason : null,
            executionEnabled: bundle.execution_enabled ?? true,
            executionBlockedReason: 'execution_blocked_reason' in bundle ? bundle.execution_blocked_reason : null,
            phase: 'waiting',
            assets,
            totalBytes,
            readyBytes: 0,
            pendingBytes: totalBytes,
            downloadProgress: 0,
            freeDiskBytes: 0,
            diskRequiredBytes: totalBytes + 20 * 1024 ** 3,
            diskSufficient: false,
            canDownload: false,
            canDelete: false,
            deleteBlockedReason: 'Mock-режим не меняет файлы моделей.',
            currentMessage: null,
            error: null,
        }
    })
    return {
        bundles,
        activeDownloadBundleId: null,
        freeDiskBytes: 0,
        minimumFreeBytes: 20 * 1024 ** 3,
    }
}

export default defineConfig({
    plugins: [tailwindcss(), solid(), {
        name: 'mock-runtime-config',
        configureServer(server) {
            server.middlewares.use((request, response, next) => {
                if (request.url === '/api/config') {
                    response.setHeader('Content-Type', 'application/json')
                    response.end(JSON.stringify({ realConnection: false }))
                    return
                }
                if (request.url === '/api/model-bundles') {
                    response.setHeader('Content-Type', 'application/json')
                    response.end(JSON.stringify(mockModelBundlesSnapshot()))
                    return
                }
                next()
            })
        },
    }],
    server: {
        host: '127.0.0.1',
        port: 4173,
        strictPort: true,
        fs: {
            allow: [projectRoot],
        },
    },
    preview: {
        host: '127.0.0.1',
        port: 4173,
        strictPort: true,
    },
    test: {
        include: ['src/**/*.test.ts'],
        environment: 'node',
        restoreMocks: true,
    },
})
