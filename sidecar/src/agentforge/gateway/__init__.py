"""Auth gateway: JWT verification, identity binding, and sensitivity policy.

Single chokepoint between the orchestrator and any tool call. Implements
record-level sensitivity decisions on metadata only (never content) and
break-the-glass propagation. See ARCHITECTURE.md §2.
"""
