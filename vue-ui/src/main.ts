import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import { migrateEncounterDraftsFromLocalStorage } from './lib/storage-migration'
import { useUiStore } from './stores/ui'

import './assets/main.css'

// HIPAA: any encounter drafts that older builds wrote to localStorage must
// be moved to sessionStorage *before* the encounter editor mounts and reads
// them. Idempotent and side-effect-free if nothing needs migrating.
migrateEncounterDraftsFromLocalStorage()

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)

// Initialize UI store (loads persisted theme + applies dark class).
const ui = useUiStore()
ui.hydrate()

// Defensive re-auth: when ANY backend call returns 401, the user's
// cached browser session is no longer valid for backend resources.
// This happens in two distinct cases:
//
//   1. Cookie was cleared (sidecar's whoami clears it on a session-store
//      miss). whoami will now report signed-out — straightforward.
//   2. Cookie + redis session both still present, but the OAuth
//      access_token they hold has expired. whoami still reports
//      signed-in (it doesn't talk to OpenEMR), but every FHIR / agent
//      call rides that expired bearer and gets 401. Without explicit
//      sign-out the SPA stays on the page and never offers re-auth.
//
// Either way, the cure is the same: sign out (drop redis session +
// cookie) and bounce to /login with the current path as redirect. The
// user clicks "Sign in with OpenEMR" again, OAuth re-runs (silently if
// the browser remembers them), a fresh access_token is minted, and
// they land back where they were.
let reauthInFlight = false
window.addEventListener('auth:unauthorized', () => {
  if (reauthInFlight) return
  reauthInFlight = true
  void (async () => {
    // Best-effort sign-out — clears redis session + cookie so the next
    // whoami reports signed-out cleanly. Then a full page navigation
    // (window.location, NOT router.replace) so the auth store's
    // hydrate-once flag resets and the guard re-evaluates from scratch
    // against the new signed-out state. router.replace would leave
    // stale isAuthenticated=true and the /login guard would bounce
    // straight back into /dashboard → 401 → loop.
    const here = router.currentRoute.value.fullPath
    const target = here === '/login' ? '/dashboard' : here
    try {
      await fetch('/auth/logout', {
        method: 'POST',
        credentials: 'same-origin',
      })
    } catch {
      // Network failure on logout — proceed to /login anyway.
    }
    const next = encodeURIComponent(target)
    window.location.assign(`/login?redirect=${next}&reason=session_expired`)
  })()
})

app.mount('#app')
