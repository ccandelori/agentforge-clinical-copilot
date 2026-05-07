import { fileURLToPath } from 'node:url'
import { mergeConfig, defineConfig, configDefaults } from 'vitest/config'
import viteConfig from './vite.config'

// vite.config exports a function (so it can read import.meta.env at
// build time); resolve it here for vitest's `mergeConfig`, which only
// accepts plain config objects.
const baseConfig = typeof viteConfig === 'function'
  ? viteConfig({ mode: 'test', command: 'serve' })
  : viteConfig

export default mergeConfig(
  baseConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      exclude: [...configDefaults.exclude, 'e2e/**'],
      root: fileURLToPath(new URL('./', import.meta.url)),
    },
  }),
)
