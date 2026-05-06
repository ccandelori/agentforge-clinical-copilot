import { WebStorageStateStore, type UserManagerSettings } from 'oidc-client-ts'

export interface AuthConfig {
  readonly baseUrl: string
  readonly site: string
  readonly clientId: string
  readonly redirectUri: string
  readonly postLogoutRedirectUri: string
  readonly scope: string
  readonly audience: string
}

type EnvLike = Partial<Record<keyof ImportMetaEnv, string>>

export function readAuthConfig(env: EnvLike = import.meta.env): AuthConfig {
  const required = (key: keyof ImportMetaEnv): string => {
    const value = env[key]
    if (value === undefined || value === null || value === '') {
      throw new Error(`Missing required env: ${String(key)}`)
    }
    return value
  }

  return {
    baseUrl: required('VITE_OPENEMR_BASE').replace(/\/+$/, ''),
    site: required('VITE_OPENEMR_SITE'),
    clientId: required('VITE_OAUTH_CLIENT_ID'),
    redirectUri: required('VITE_OAUTH_REDIRECT_URI'),
    postLogoutRedirectUri: required('VITE_OAUTH_POST_LOGOUT_REDIRECT_URI'),
    scope: required('VITE_OAUTH_SCOPE'),
    audience: required('VITE_OAUTH_AUDIENCE'),
  }
}

export function toUserManagerSettings(config: AuthConfig): UserManagerSettings {
  return {
    authority: `${config.baseUrl}/oauth2/${config.site}`,
    client_id: config.clientId,
    redirect_uri: config.redirectUri,
    post_logout_redirect_uri: config.postLogoutRedirectUri,
    response_type: 'code',
    scope: config.scope,
    automaticSilentRenew: true,
    monitorSession: false,
    extraQueryParams: { aud: config.audience },
    userStore: new WebStorageStateStore({ store: window.sessionStorage }),
    stateStore: new WebStorageStateStore({ store: window.sessionStorage }),
  }
}
