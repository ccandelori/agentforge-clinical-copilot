import { createRouter, createWebHistory, type RouteLocationNormalized } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import LoginView from '../views/LoginView.vue'
import OAuthCallbackView from '../views/OAuthCallbackView.vue'
import { useAuthStore } from '@/stores/auth'

// Routes added incrementally per Task 38 subtasks. T38.2 introduces /login,
// /auth/callback, and the requiresAuth meta flag; PatientDashboardView
// (T38.3) is the first surface that flips requiresAuth on. The cards
// (T38.4–T38.9) and AgentForge drawer (T38.10) build into that view.
const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'home',
      component: HomeView,
    },
    {
      path: '/login',
      name: 'login',
      component: LoginView,
      meta: { requiresAuth: false },
    },
    {
      path: '/auth/callback',
      name: 'oauth-callback',
      component: OAuthCallbackView,
      meta: { requiresAuth: false },
    },
  ],
})

let hydrated = false

router.beforeEach(async (to: RouteLocationNormalized) => {
  const auth = useAuthStore()

  if (!hydrated) {
    hydrated = true
    await auth.hydrate()
  }

  const requiresAuth = to.meta.requiresAuth !== false && to.name !== 'login' && to.name !== 'oauth-callback'
  if (requiresAuth && !auth.isAuthenticated) {
    return { name: 'login', query: { next: to.fullPath } }
  }
})

export default router
