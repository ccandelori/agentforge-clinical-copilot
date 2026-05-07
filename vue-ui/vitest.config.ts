import { defineConfig } from 'vitest/config'

// Note: when adding the @vitejs/plugin-vue plugin here, ensure vitest's
// bundled vite version matches the project's vite version (currently 6.x
// requires vitest >= 3). Until then this config only configures the runner.
export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.spec.ts', 'src/**/*.test.ts'],
  },
})
