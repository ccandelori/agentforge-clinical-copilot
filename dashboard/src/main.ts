import 'bootstrap/dist/css/bootstrap.min.css'
import 'bootstrap-icons/font/bootstrap-icons.css'
// tokens.css must come AFTER bootstrap.min.css so its --bs-* overrides win.
import './assets/tokens.css'
import './assets/main.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createBootstrap } from 'bootstrap-vue-next'

import App from './App.vue'
import router from './router'
import { usePreferencesStore } from './stores/preferences'

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(createBootstrap())

// Apply persisted theme before mount so the first paint matches the
// user's preference (and tracks `prefers-color-scheme` if 'system').
usePreferencesStore().hydrate()

app.mount('#app')
