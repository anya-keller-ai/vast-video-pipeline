import js from '@eslint/js'
import solid from 'eslint-plugin-solid/configs/typescript'
import tseslint from 'typescript-eslint'

export default tseslint.config(
    {
        ignores: ['dist/**', 'node_modules/**', 'playwright-report/**', 'test-results/**'],
    },
    js.configs.recommended,
    ...tseslint.configs.recommended,
    {
        files: ['src/**/*.{ts,tsx}'],
        ...solid,
        rules: {
            ...solid.rules,
            indent: ['error', 4, { SwitchCase: 1 }],
            quotes: ['error', 'single', { avoidEscape: true }],
            semi: ['error', 'never'],
        },
    },
)
