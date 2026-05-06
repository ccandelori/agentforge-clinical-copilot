import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'

// Routes added incrementally per Task 38 subtasks. Initial scaffold (T38.1)
// ships only the home placeholder; LoginView lands with T38.2 (OAuth2 flow);
// PatientDashboardView lands with T38.3 (patient header) and is the surface
// the cards (T38.4–T38.9) and AgentForge drawer (T38.10) build into.
const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'home',
      component: HomeView,
    },
  ],
})

export default router
