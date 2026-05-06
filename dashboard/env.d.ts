/// <reference types="vite/client" />

interface ImportMetaEnv {
  // BFF target host. Used only by `vite.config.ts`'s dev-server proxy
  // to forward /auth/* and /api/* to the sidecar; not consumed by
  // dashboard runtime code (which speaks to relative paths).
  readonly VITE_SIDECAR_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
