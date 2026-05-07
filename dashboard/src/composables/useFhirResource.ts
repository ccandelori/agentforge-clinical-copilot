import { ref, type Ref } from 'vue'

// FHIR fetch primitive shared by every card. Auth is carried by an
// HttpOnly session cookie set by the sidecar's /auth/callback handler;
// JS never sees the OAuth2 access token. The composable auto-fires on
// creation and exposes refetch() for manual refresh.

export type FhirResourceStatus = 'idle' | 'loading' | 'success' | 'error'

export interface UseFhirResource<T> {
  status: Ref<FhirResourceStatus>
  data: Ref<T | null>
  error: Ref<Error | null>
  refetch: () => Promise<void>
}

export function useFhirResource<T>(path: string): UseFhirResource<T> {
  const status = ref<FhirResourceStatus>('idle')
  const data = ref<T | null>(null) as Ref<T | null>
  const error = ref<Error | null>(null)

  async function refetch(): Promise<void> {
    status.value = 'loading'
    error.value = null
    try {
      const res = await fetch(path, {
        credentials: 'same-origin',
        headers: { Accept: 'application/fhir+json' },
      })
      if (!res.ok) {
        throw new Error(`${path} returned ${res.status}`)
      }
      data.value = (await res.json()) as T
      status.value = 'success'
    } catch (caught) {
      data.value = null
      error.value = caught instanceof Error ? caught : new Error(String(caught))
      status.value = 'error'
    }
  }

  void refetch()

  return { status, data, error, refetch }
}
