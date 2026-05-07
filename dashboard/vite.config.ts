import { fileURLToPath, URL } from 'node:url'

import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const sidecarBase = env.VITE_SIDECAR_BASE ?? 'http://localhost:8000'

  return {
    plugins: [vue(), vueDevTools()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      // Same-origin proxy onto the AgentForge sidecar BFF. The dashboard
      // is a public SPA and cannot hold the OAuth2 client_secret without
      // leaking it to anyone with DevTools — the sidecar (FastAPI) holds
      // the secret server-side and exposes:
      //
      //   /auth/login          — start OAuth2 flow (PKCE; Pkce verifier in Redis)
      //   /auth/callback       — finalize: code → tokens → session cookie
      //   /auth/whoami         — current session info (or {authenticated:false})
      //   /auth/logout         — clear session
      //   /api/fhir/{path:path} — proxy FHIR reads with the user's bearer
      //
      // Routing both /auth/* and /api/* through the same proxy keeps the
      // browser's view of the world same-origin so cookies set by the
      // sidecar (HttpOnly, SameSite=Lax) ride correctly on subsequent
      // requests. Production (T38.14) serves the dashboard from the
      // sidecar host so the same relative paths work without any proxy.
      proxy: {
        '/auth': {
          target: sidecarBase,
          changeOrigin: true,
        },
        '/api': {
          target: sidecarBase,
          changeOrigin: true,
        },
      },
    },
  }
})
