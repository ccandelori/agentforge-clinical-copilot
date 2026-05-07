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

app.mount('#app')
