"""Langfuse integration with HMAC-keyed pseudonyms for engineering traces.

Logs the shape of an interaction (timing, status, hashes) but never its
substance (no patient_id, no PHI bodies, no prompt content). Distinct from
OpenEMR's legal audit log, which is the medical-record system of record.
See ARCHITECTURE.md S7.
"""

from __future__ import annotations

from agentforge.observability.hmac_hash import (
    PSEUDONYM_HEX_LENGTH,
    hash_payload,
    pseudonymize,
)
from agentforge.observability.langfuse_client import AgentLangfuse
from agentforge.observability.null_client import NullLangfuseClient
from agentforge.observability.protocols import LangfuseClient, TraceHandle

__all__ = [
    "PSEUDONYM_HEX_LENGTH",
    "AgentLangfuse",
    "LangfuseClient",
    "NullLangfuseClient",
    "TraceHandle",
    "hash_payload",
    "pseudonymize",
]
