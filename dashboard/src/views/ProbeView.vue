<script setup lang="ts">
import { onMounted, ref } from 'vue'

// Throwaway probe — verifies the sidecar BFF FHIR proxy works end-to-end
// against dev-easy. Hits /api/fhir/* (cookie-authenticated, no JS-side
// token) and surfaces status + body. Delete after T38.3 lands.

interface ProbeResult {
  label: string
  url: string
  status: number
  ok: boolean
  contentType: string | null
  body: unknown
  error: string | null
}

const results = ref<ProbeResult[]>([])

async function probe(label: string, url: string): Promise<ProbeResult> {
  try {
    const res = await fetch(url, {
      credentials: 'same-origin',
      headers: { Accept: 'application/fhir+json' },
    })
    let body: unknown = null
    try {
      body = await res.json()
    } catch {
      body = await res.text().catch(() => '<unreadable>')
    }
    return {
      label,
      url,
      status: res.status,
      ok: res.ok,
      contentType: res.headers.get('content-type'),
      body,
      error: null,
    }
  } catch (caught) {
    return {
      label,
      url,
      status: 0,
      ok: false,
      contentType: null,
      body: null,
      error: caught instanceof Error ? caught.message : String(caught),
    }
  }
}

onMounted(async () => {
  const probes: { label: string; url: string }[] = [
    { label: 'FHIR CapabilityStatement', url: '/api/fhir/metadata' },
    { label: 'FHIR Patient search', url: '/api/fhir/Patient?_count=10' },
    { label: 'FHIR Practitioner search', url: '/api/fhir/Practitioner?_count=5' },
  ]
  for (const p of probes) {
    results.value.push(await probe(p.label, p.url))
  }
})
</script>

<template>
  <main class="container py-4">
    <h1 class="h4 mb-1">FHIR Probe (BFF)</h1>
    <p class="text-muted small">
      Diagnostic — exercises /api/fhir/* through the sidecar BFF. Delete
      after T38.3 decision.
    </p>

    <section v-for="r in results" :key="r.url" class="mb-4">
      <h2 class="h6">{{ r.label }}</h2>
      <div class="small mb-1"><code>{{ r.url }}</code></div>
      <div class="mb-1">
        <strong>Status:</strong>
        <span :class="r.ok ? 'text-success' : 'text-danger'">
          {{ r.status === 0 ? 'fetch failed' : r.status }}
          {{ r.ok ? 'OK' : '' }}
        </span>
      </div>
      <div v-if="r.error !== null" class="alert alert-danger small">{{ r.error }}</div>
      <details>
        <summary class="small text-muted">body</summary>
        <pre class="bg-light p-2 small" style="max-height: 18rem; overflow: auto">{{ JSON.stringify(r.body, null, 2) }}</pre>
      </details>
    </section>
  </main>
</template>
