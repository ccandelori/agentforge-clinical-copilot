import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useAgentDrawer } from '@/stores/agentDrawer'

// Pinia store powering the AgentForge drawer (T38.10).
//
// Conversation scoping per project_panel_placement.md:
//   * Chart mode is keyed by `chart:<pid>`.
//   * Intake mode is keyed by `intake:<documentId>`.
//   * Research mode lives at a single global scope `research:global`.
//
// History is in-memory only (lost on page reload). When the active
// patient changes WHILE the drawer is open with a non-empty Chart
// conversation, the store stages a `pendingPatientChange` instead of
// switching outright. UI renders a hard-interrupt overlay and calls
// `resolvePatientChange()` once the user picks Switch/Stay/Fresh.

describe('useAgentDrawer', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('starts closed in research mode with no patient or document', () => {
    const store = useAgentDrawer()
    expect(store.open).toBe(false)
    expect(store.mode).toBe('research')
    expect(store.activePatient).toBeNull()
    expect(store.activeDocument).toBeNull()
    expect(store.pendingPatientChange).toBeNull()
    expect(store.canChart).toBe(false)
    expect(store.currentMessages).toEqual([])
  })

  describe('open/close/toggle', () => {
    it('open() flips to open', () => {
      const store = useAgentDrawer()
      store.openDrawer()
      expect(store.open).toBe(true)
    })

    it('close() flips back to closed', () => {
      const store = useAgentDrawer()
      store.openDrawer()
      store.close()
      expect(store.open).toBe(false)
    })

    it('toggle() flips state in both directions', () => {
      const store = useAgentDrawer()
      store.toggle()
      expect(store.open).toBe(true)
      store.toggle()
      expect(store.open).toBe(false)
    })
  })

  describe('mode switching', () => {
    it('canChart becomes true once an active patient is set', () => {
      const store = useAgentDrawer()
      expect(store.canChart).toBe(false)
      store.setActivePatient('p1')
      expect(store.canChart).toBe(true)
    })

    it('setMode("research") always works', () => {
      const store = useAgentDrawer()
      store.setMode('research')
      expect(store.mode).toBe('research')
    })

    it('setMode("chart") is a no-op when no patient is active', () => {
      const store = useAgentDrawer()
      store.setMode('chart')
      expect(store.mode).toBe('research')
    })

    it('setMode("chart") works after an active patient is set', () => {
      const store = useAgentDrawer()
      store.setActivePatient('p1')
      store.setMode('chart')
      expect(store.mode).toBe('chart')
    })

    it('clearing the active patient demotes Chart mode back to Research', () => {
      const store = useAgentDrawer()
      store.setActivePatient('p1')
      store.setMode('chart')
      store.setActivePatient(null)
      expect(store.canChart).toBe(false)
      expect(store.mode).toBe('research')
    })
  })

  describe('conversation scoping', () => {
    it('addUserTurn appends to the current scope only', () => {
      const store = useAgentDrawer()
      store.setActivePatient('p1')
      store.setMode('chart')
      store.addUserTurn('chart hello')
      store.setMode('research')
      store.addUserTurn('research hi')

      expect(store.currentMessages).toHaveLength(1)
      expect(store.currentMessages[0]?.text).toBe('research hi')

      store.setMode('chart')
      expect(store.currentMessages).toHaveLength(1)
      expect(store.currentMessages[0]?.text).toBe('chart hello')
    })

    it('addAssistantTurn flags the message role correctly', () => {
      const store = useAgentDrawer()
      store.addUserTurn('q')
      store.addAssistantTurn('a')

      expect(store.currentMessages.map((m) => m.role)).toEqual([
        'user',
        'assistant',
      ])
    })

    it('chart conversations are keyed per-patient', () => {
      const store = useAgentDrawer()
      store.setActivePatient('p1')
      store.setMode('chart')
      store.addUserTurn('about p1')

      // Switching patients while in Research mode skips the conflict
      // policy entirely (Chart-mode-with-progress is the only path that
      // stages a pendingPatientChange — that is exercised below).
      store.setMode('research')
      store.setActivePatient('p2')
      store.setMode('chart')
      expect(store.currentMessages).toEqual([])

      store.setMode('research')
      store.setActivePatient('p1')
      store.setMode('chart')
      expect(store.currentMessages).toHaveLength(1)
      expect(store.currentMessages[0]?.text).toBe('about p1')
    })

    it('intake conversations are keyed per-document', () => {
      const store = useAgentDrawer()
      store.setActiveDocument('doc-1')
      store.setMode('intake')
      store.addUserTurn('about doc-1')

      store.setActiveDocument('doc-2')
      expect(store.currentMessages).toEqual([])

      store.setActiveDocument('doc-1')
      expect(store.currentMessages).toHaveLength(1)
    })
  })

  describe('patient-context conflict', () => {
    it('switching patients with an empty Chart conversation is immediate (no pending)', () => {
      const store = useAgentDrawer()
      store.setActivePatient('p1')
      store.setMode('chart')
      // no messages added — immediate switch is allowed
      store.setActivePatient('p2')

      expect(store.activePatient).toBe('p2')
      expect(store.pendingPatientChange).toBeNull()
    })

    it('switching patients in Research mode is immediate even with chart progress', () => {
      const store = useAgentDrawer()
      store.setActivePatient('p1')
      store.setMode('chart')
      store.addUserTurn('progress on p1')
      store.setMode('research')

      store.setActivePatient('p2')
      expect(store.activePatient).toBe('p2')
      expect(store.pendingPatientChange).toBeNull()
    })

    it('switching patients in Chart mode with progress stages a pendingPatientChange', () => {
      const store = useAgentDrawer()
      store.setActivePatient('p1')
      store.setMode('chart')
      store.addUserTurn('progress on p1')

      store.setActivePatient('p2')
      // active stays on p1 until resolution
      expect(store.activePatient).toBe('p1')
      expect(store.pendingPatientChange).toEqual({ from: 'p1', to: 'p2' })
    })

    it('hasStaleConversation reports whether a non-active patient already has chart history', () => {
      const store = useAgentDrawer()
      store.setActivePatient('p1')
      store.setMode('chart')
      store.addUserTurn('progress on p1')
      store.setActivePatient('p2') // pending

      expect(store.hasStaleConversation('p2')).toBe(false)

      // resolve to p2, build progress, swing back to p1, then attempt p2 again
      store.resolvePatientChange('switch')
      store.addUserTurn('progress on p2')
      // p1 still has its messages from earlier
      expect(store.hasStaleConversation('p1')).toBe(true)
    })

    it('resolvePatientChange("switch") moves active to the pending target', () => {
      const store = useAgentDrawer()
      store.setActivePatient('p1')
      store.setMode('chart')
      store.addUserTurn('q')
      store.setActivePatient('p2')

      store.resolvePatientChange('switch')
      expect(store.activePatient).toBe('p2')
      expect(store.pendingPatientChange).toBeNull()
    })

    it('resolvePatientChange("stay") keeps the original patient and clears the pending change', () => {
      const store = useAgentDrawer()
      store.setActivePatient('p1')
      store.setMode('chart')
      store.addUserTurn('q')
      store.setActivePatient('p2')

      store.resolvePatientChange('stay')
      expect(store.activePatient).toBe('p1')
      expect(store.pendingPatientChange).toBeNull()
    })

    it('resolvePatientChange("fresh") wipes the target chart history then switches', () => {
      const store = useAgentDrawer()
      store.setActivePatient('p1')
      store.setMode('chart')
      store.addUserTurn('p1 q')

      // build stale history on p2
      store.setActivePatient('p2')
      store.resolvePatientChange('switch')
      store.addUserTurn('p2 stale')

      // back to p1 — stages a pending change
      store.setActivePatient('p1')
      store.resolvePatientChange('switch')
      // now from p1 hop back to p2; p2 has stale history so a fresh start applies
      store.addUserTurn('p1 followup')
      store.setActivePatient('p2')
      expect(store.pendingPatientChange).toEqual({ from: 'p1', to: 'p2' })

      store.resolvePatientChange('fresh')
      expect(store.activePatient).toBe('p2')
      expect(store.pendingPatientChange).toBeNull()
      expect(store.currentMessages).toEqual([])
    })

    it('resolvePatientChange is a no-op when nothing is pending', () => {
      const store = useAgentDrawer()
      store.setActivePatient('p1')
      // no pending change
      store.resolvePatientChange('switch')
      expect(store.activePatient).toBe('p1')
      expect(store.pendingPatientChange).toBeNull()
    })
  })
})
