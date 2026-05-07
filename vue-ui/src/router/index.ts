import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const AppShell = () => import('@/layouts/AppShell.vue')

// Placeholder views — Wave 2 agents will replace these in their respective
// folders (see vue-ui/AGENT-CONTRACT.md).
const DashboardHome = () => import('@/views/_placeholders/DashboardHome.vue')
const PatientList = () => import('@/views/patients/PatientList.vue')
const PatientDashboard = () => import('@/views/patients/PatientDashboard.vue')
const CalendarView = () => import('@/views/calendar/CalendarView.vue')
const EncounterEditor = () => import('@/views/encounters/EncounterEditor.vue')
const SettingsView = () => import('@/views/settings/SettingsView.vue')
const LoginView = () => import('@/views/auth/LoginView.vue')
const NotFound = () => import('@/views/_placeholders/NotFound.vue')

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: LoginView,
    meta: { requiresAuth: false },
  },
  {
    path: '/',
    component: AppShell,
    meta: { requiresAuth: true },
    children: [
      { path: '', redirect: { name: 'dashboard' } },
      { path: 'dashboard', name: 'dashboard', component: DashboardHome },
      { path: 'patients', name: 'patients', component: PatientList },
      {
        path: 'patients/:id',
        name: 'patient-dashboard',
        component: PatientDashboard,
        props: true,
      },
      { path: 'calendar', name: 'calendar', component: CalendarView },
      {
        path: 'encounters/:id',
        name: 'encounter',
        component: EncounterEditor,
        props: true,
      },
      { path: 'settings', name: 'settings', component: SettingsView },
    ],
  },
  {
    path: '/:catchAll(.*)*',
    name: 'not-found',
    component: NotFound,
    meta: { requiresAuth: false },
  },
]

const router = createRouter({
  // BASE_URL is set from vite's `base` config:
  //   '/'           in dev (default)
  //   '/dashboard/' in production (T38.14 cutover — Apache hosts the
  //                 SPA at /dashboard/ on the same origin as OpenEMR).
  // Passing it in tells vue-router to strip the prefix from history
  // URLs so the route table stays prefix-agnostic.
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
  scrollBehavior(_to, _from, saved) {
    return saved ?? { top: 0 }
  },
})

// Hydrate the auth store exactly once per app load. The guard awaits
// `auth.hydrate()` so route render only kicks off after we know the
// session state from the sidecar.
let hydrated = false

router.beforeEach(async (to) => {
  const auth = useAuthStore()

  if (!hydrated) {
    hydrated = true
    await auth.hydrate()
  }

  const requiresAuth = to.matched.some(
    (record) => record.meta.requiresAuth !== false,
  )

  if (requiresAuth && !auth.isAuthenticated) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }

  if (to.name === 'login' && auth.isAuthenticated) {
    return { name: 'dashboard' }
  }

  return true
})

export default router
