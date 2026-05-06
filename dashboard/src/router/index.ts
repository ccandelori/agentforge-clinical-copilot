import { createRouter, createWebHistory, type RouteLocationNormalized } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import LoginView from '../views/LoginView.vue'
import { useAuthStore } from '@/stores/auth'

// Routes added incrementally per Task 38 subtasks. T38.2 v2 ships the
// BFF flow: /login renders the sign-in CTA; /auth/callback is owned
// by the sidecar (Vite's proxy intercepts before this router sees it).
// T38.3 adds /patient/:pid; T38.4–T38.9 are cards composed inside it.
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
      path: '/probe',
      name: 'probe',
      component: () => import('../views/ProbeView.vue'),
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

  const requiresAuth = to.meta.requiresAuth !== false && to.name !== 'login'
  if (requiresAuth && !auth.isAuthenticated) {
    return { name: 'login', query: { next: to.fullPath } }
  }
})

export default router
