import { ref, type Ref } from 'vue'

// Dashboard half of the auth bridge described in
// docs/adr/0001-dashboard-auth-bridging.md. POSTs to the sidecar's
// /api/agent/turn route over the BFF session cookie. The sidecar
// owns identity + JWT minting + RequestContext construction; this
// composable just shapes the body and surfaces a typed result.
//
// Tokens are never visible to JS. ``credentials: 'same-origin'``
// instructs fetch() to attach the HttpOnly session cookie set by
// /auth/callback so the sidecar can authenticate the request.

export type AgentTurnStatus = 'idle' | 'loading' | 'success' | 'error'

export interface AgentTurnRequest {
  message: string
  patient_id: number
  session_id?: string
}

interface AgentTurnResponseBody {
  reply: string
}

export interface UseAgentTurn {
  status: Ref<AgentTurnStatus>
  error: Ref<Error | null>
  send: (req: AgentTurnRequest) => Promise<string>
}

export function useAgentTurn(): UseAgentTurn {
  const status = ref<AgentTurnStatus>('idle')
  const error = ref<Error | null>(null)

  async function send(req: AgentTurnRequest): Promise<string> {
    status.value = 'loading'
    error.value = null

    const body: Record<string, unknown> = {
      message: req.message,
      patient_id: req.patient_id,
    }
    if (req.session_id !== undefined) {
      body.session_id = req.session_id
    }

    try {
      const res = await fetch('/api/agent/turn', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        throw new Error(
          `/api/agent/turn returned ${res.status}`,
        )
      }
      const parsed = (await res.json()) as Partial<AgentTurnResponseBody>
      if (typeof parsed.reply !== 'string') {
        throw new Error('Agent turn response missing reply field')
      }
      status.value = 'success'
      return parsed.reply
    } catch (caught) {
      const err = caught instanceof Error ? caught : new Error(String(caught))
      error.value = err
      status.value = 'error'
      throw err
    }
  }

  return { status, error, send }
}
