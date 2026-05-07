import { fileURLToPath, URL } from 'node:url'

import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const sidecarBase = env.VITE_SIDECAR_BASE ?? 'http://localhost:8000'

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      port: 5173,
      strictPort: false,
      host: '127.0.0.1',
      // Same-origin proxy onto the AgentForge sidecar BFF. The vue-ui
      // SPA cannot hold the OAuth2 client_secret without leaking it to
      // anyone with DevTools — the sidecar (FastAPI) holds the secret
      // server-side and exposes:
      //
      //   /auth/login          — start OAuth2 flow (PKCE; verifier in Redis)
      //   /auth/callback       — finalize: code → tokens → session cookie
      //   /auth/whoami         — current session info (or {authenticated:false})
      //   /auth/logout         — clear session
      //   /api/fhir/{path:path} — proxy FHIR reads with the user's bearer
      //   /api/agent/turn      — AgentForge turn endpoint
      //
      // Routing both /auth/* and /api/* through the same proxy keeps the
      // browser's view of the world same-origin so cookies set by the
      // sidecar (HttpOnly, SameSite=Lax) ride correctly on subsequent
      // requests. Production (T38.14) serves vue-ui from the sidecar
      // host so the same relative paths work without any proxy.
      proxy: {
        '/auth': {
          target: sidecarBase,
          changeOrigin: true,
          secure: false,
        },
        '/api': {
          target: sidecarBase,
          changeOrigin: true,
          secure: false,
        },
      },
    },
    preview: {
      port: 5173,
    },
  }
})
