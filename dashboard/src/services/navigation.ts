// Thin indirection over `window.location.assign` so the auth store's
// browser-redirect actions (sign-in / sign-out) are testable without
// having to monkey-patch `window.location`. Vitest specs mock this
// module via `vi.mock('@/services/navigation', ...)`.

export function navigateTo(url: string): void {
  window.location.assign(url)
}
