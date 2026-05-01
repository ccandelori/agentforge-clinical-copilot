"""Typed Python adapters over OpenEMR's FHIR R4 and internal REST APIs.

Tools never read MariaDB directly (preserves audit-log inheritance) and
never raise exceptions to the orchestrator — failures are returned as
ToolResult values with a non-ok status. See ARCHITECTURE.md §4.
"""

from agentforge.tools.demographics import (
    DEMOGRAPHICS_TOOL_SPEC,
    DemographicsFetcher,
    DemographicsPayload,
    DemographicsResult,
)
from agentforge.tools.dtos import ToolResult, ToolResultMetadata
from agentforge.tools.medications import (
    MEDICATIONS_TOOL_SPEC,
    MedicationItem,
    MedicationsFetcher,
    MedicationsPayload,
    MedicationsResult,
)
from agentforge.tools.problems import (
    PROBLEMS_TOOL_SPEC,
    ProblemItem,
    ProblemsFetcher,
    ProblemsPayload,
    ProblemsResult,
)

__all__ = [
    "DEMOGRAPHICS_TOOL_SPEC",
    "MEDICATIONS_TOOL_SPEC",
    "PROBLEMS_TOOL_SPEC",
    "DemographicsFetcher",
    "DemographicsPayload",
    "DemographicsResult",
    "MedicationItem",
    "MedicationsFetcher",
    "MedicationsPayload",
    "MedicationsResult",
    "ProblemItem",
    "ProblemsFetcher",
    "ProblemsPayload",
    "ProblemsResult",
    "ToolResult",
    "ToolResultMetadata",
]
