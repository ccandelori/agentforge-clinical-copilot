"""Auth gateway: JWT verification, identity binding, and sensitivity policy.

Single chokepoint between the orchestrator and any tool call. Implements
record-level sensitivity decisions on metadata only (never content) and
break-the-glass propagation. See ARCHITECTURE.md §2.
"""

from agentforge.gateway.auth_gateway import (
    AuthGateway,
    RecordMetadata,
    RequestContext,
)
from agentforge.gateway.policy import RecordClassRule, SensitivityPolicy
from agentforge.gateway.policy_loader import load_sensitivity_policy
from agentforge.gateway.policy_reader import fetch_sensitivity_rules

__all__ = [
    "AuthGateway",
    "RecordClassRule",
    "RecordMetadata",
    "RequestContext",
    "SensitivityPolicy",
    "fetch_sensitivity_rules",
    "load_sensitivity_policy",
]
