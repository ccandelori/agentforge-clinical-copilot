import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig, type UserConfig } from 'vitest/config'

// vitest 2.x ships its own bundled vite (5.x), while the workspace runs
// vite 6.x for the app build. The two installs duplicate the
// `Plugin<Api>` type, so the @vitejs/plugin-vue plugin (resolved against
// workspace vite 6) is structurally — but not nominally — assignable to
// vitest's plugin slot.
//
// The runtime shape is identical, so we narrow at the boundary:
// build the plugins array with the workspace-vite type, then cast
// through unknown to vitest's UserConfig['plugins'] slot. Removing the
// cast is a follow-up once vitest 3 + vite 6 align.
const plugins = [vue()] as unknown as UserConfig['plugins']

export default defineConfig({
    plugins,
    resolve: {
        alias: {
            '@': fileURLToPath(new URL('./src', import.meta.url)),
        },
    },
    test: {
        environment: 'jsdom',
        globals: true,
        include: ['src/**/*.spec.ts', 'src/**/*.test.ts'],
    },
})
