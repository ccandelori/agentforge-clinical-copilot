import { createRouter, createWebHistory, type RouteLocationNormalized } from 'vue-router'
import LoginView from '../views/LoginView.vue'
import PatientPickerView from '../views/PatientPickerView.vue'
import { useAuthStore } from '@/stores/auth'

// T38.2 v2 ships the BFF flow: /login renders the sign-in CTA;
// /auth/callback is owned by the sidecar (Vite's proxy intercepts
// before this router sees it). T38.3 adds the implicit patient picker
// at / and the per-patient dashboard at /patient/:pid; T38.4–T38.9
// drop card components into PatientDashboardView.
const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'patient-picker',
      component: PatientPickerView,
    },
    {
      path: '/patient/:pid',
      name: 'patient',
      component: () => import('../views/PatientDashboardView.vue'),
      props: true,
    },
    {
      path: '/login',
      name: 'login',
      component: LoginView,
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

  const requiresAuth = to.meta.requiresAuth !== false && to.name !== 'login'
  if (requiresAuth && !auth.isAuthenticated) {
    return { name: 'login', query: { next: to.fullPath } }
  }
})

export default router
