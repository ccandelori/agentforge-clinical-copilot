"""Storage primitives for the AgentForge sidecar.

This package owns the Redis-backed PHI stores defined in
ARCHITECTURE.md S7.1: session memory (75 min TTL) and tool result cache
(60 s TTL). Both are encrypted at rest by the BAA-covered Redis
deployment and keyed to prevent cross-session leakage.
"""
