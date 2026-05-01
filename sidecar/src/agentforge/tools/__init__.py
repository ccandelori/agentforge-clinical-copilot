"""Typed Python adapters over OpenEMR's FHIR R4 and internal REST APIs.

Tools never read MariaDB directly (preserves audit-log inheritance) and
never raise exceptions to the orchestrator — failures are returned as
ToolResult values with a non-ok status. See ARCHITECTURE.md §4.
"""

from agentforge.tools.demographics import DemographicsPayload, DemographicsResult
from agentforge.tools.dtos import ToolResult, ToolResultMetadata

__all__ = [
    "DemographicsPayload",
    "DemographicsResult",
    "ToolResult",
    "ToolResultMetadata",
]
