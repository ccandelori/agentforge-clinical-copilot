/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_OPENEMR_BASE: string
  readonly VITE_OPENEMR_SITE: string
  readonly VITE_OAUTH_CLIENT_ID: string
  readonly VITE_OAUTH_REDIRECT_URI: string
  readonly VITE_OAUTH_POST_LOGOUT_REDIRECT_URI: string
  readonly VITE_OAUTH_SCOPE: string
  readonly VITE_OAUTH_AUDIENCE: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
