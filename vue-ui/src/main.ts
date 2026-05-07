import { createApp } from 'vue'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'
import { useUiStore } from './stores/ui'

import './assets/main.css'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)

// Initialize UI store (loads persisted theme + applies dark class).
const ui = useUiStore()
ui.hydrate()

app.mount('#app')
