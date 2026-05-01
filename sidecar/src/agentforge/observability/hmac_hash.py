"""HMAC-SHA256 pseudonyms and content hashes for Langfuse traces.

Two operations sit on the same primitive:

  * ``pseudonymize`` turns a user_id or patient_id into a stable opaque
    token so traces can be joined without ever surfacing the original
    identifier in the trace store.
  * ``hash_payload`` turns a tool's args or result into a fixed-length
    digest the verifier and orchestrator can attach to spans, so we can
    answer "did the same args produce the same result?" without storing
    the bodies.

Both share a single per-environment HMAC key (`LANGFUSE_HMAC_KEY` /
`HMAC_KEY` env var) so a single rotation flips every pseudonym in the
deployment at once. Without the key, neither pseudonyms nor payload
hashes are reversible to OpenEMR identifiers or PHI bodies — that is the
guarantee Langfuse relies on. See ARCHITECTURE.md S7.2.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Final

# 16 hex chars (8 bytes / 64 bits of entropy) is the trade-off:
#   * collision probability across our trace volume (≤ 10^7 IDs/year per
#     environment) stays well below 10^-4 by the birthday bound;
#   * Langfuse trace UIs and grep workflows stay readable;
#   * an attacker who steals a trace dump still cannot brute-force the
#     pre-image without the HMAC key (bounded by HMAC-SHA256 strength,
#     not the truncation length).
# Anything shorter starts colliding; anything longer just hurts ergonomics.
PSEUDONYM_HEX_LENGTH: Final[int] = 16


def pseudonymize(raw: str | int, key: bytes) -> str:
    """Return a stable, irreversible token for a user or patient identifier.

    Truncated to :data:`PSEUDONYM_HEX_LENGTH` hex characters. Same key +
    same value always produces the same token; different keys produce
    different tokens (this is the rotation property — a quarterly key
    swap re-randomises every pseudonym).

    Integer and string inputs are normalised through ``str()`` so that
    ``pseudonymize(42, k) == pseudonymize("42", k)``. Callers that want
    to distinguish those cases must pre-encode the type discriminator.
    """
    if not key:
        raise ValueError("HMAC key must be non-empty bytes")
    payload = str(raw).encode("utf-8")
    digest = hmac.new(key, payload, hashlib.sha256).hexdigest()
    return digest[:PSEUDONYM_HEX_LENGTH]


def hash_payload(payload: bytes | str | dict[str, object], key: bytes) -> str:
    """Return a stable hash of a tool args / result body.

    ``dict`` payloads are JSON-encoded with sorted keys so two calls with
    the same data always hash identically regardless of insertion order.
    The output uses the full HMAC-SHA256 hex digest (64 chars); callers
    that want a short identifier can truncate at the call site, but the
    default is full-width because hashes here are the only join key
    between trace spans and the underlying call.
    """
    if not key:
        raise ValueError("HMAC key must be non-empty bytes")
    if isinstance(payload, bytes):
        body = payload
    elif isinstance(payload, str):
        body = payload.encode("utf-8")
    else:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, body, hashlib.sha256).hexdigest()
