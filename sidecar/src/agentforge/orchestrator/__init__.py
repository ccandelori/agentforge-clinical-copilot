"""Minimal MVP orchestrator: 3-tool loop, no verifier, no parallel dispatch.

The full ARCHITECTURE.md §3 design has Planner / ToolDispatcher / Synthesizer /
Verifier nodes. This MVP collapses all of that into one tight loop:

  user_message -> Claude -> (tool_use? -> dispatch -> tool_result -> Claude)+ -> text

Three tools are registered: get_demographics, get_active_problems, and
get_active_medications. We rely on Claude's stop_reason to know when to
stop tool-calling; we cap the iteration count so a misbehaving model can't
burn the deadline. When the verifier ships (Task 28) and the rest of the
tools land (Tasks 17-25), the loop here gets replaced by a real LangGraph
state machine.
"""

from __future__ import annotations

import json
from typing import Final

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.client import LLMClient
from agentforge.llm.types import Message
from agentforge.tools.demographics import DEMOGRAPHICS_TOOL_SPEC, DemographicsFetcher
from agentforge.tools.medications import MEDICATIONS_TOOL_SPEC, MedicationsFetcher
from agentforge.tools.problems import PROBLEMS_TOOL_SPEC, ProblemsFetcher

SYSTEM_PROMPT: Final[str] = """\
You are AgentForge, a clinical co-pilot embedded inside OpenEMR. A clinician \
is asking about a single patient whose chart is currently open in their \
browser. You answer questions grounded in that patient's record by calling \
tools — never from memory or speculation.

You have three tools:
  - get_demographics      : name, DOB, sex, preferred language
  - get_active_problems   : current diagnoses / problem list
  - get_active_medications: currently active medications (with begin/end dates)

Rules:
1. When the user asks about ANY patient information, call the relevant tool \
first. Don't pre-narrate ("Let me look that up…"); just call.
2. If a question needs multiple tools (e.g. "summarize this patient", \
"any medication conflicts with their conditions?"), call them all — \
Claude's tool_use can emit several calls per turn.
3. After tool results return, briefly cite which tools you drew from in \
plain language ("Per the active problem list and medications…"). Don't \
quote tool JSON to the user; translate it into a clinical summary.
4. Be concise, clinical, non-speculative. Use medical terminology where \
appropriate. Don't hedge with "as an AI…".
5. If a tool returns an error or empty result, say so plainly. Do not \
invent data to fill gaps. An empty problem list means "no active problems \
recorded," not "the patient is healthy."
6. If the user asks about something you don't have a tool for (lab \
results, vitals trends, allergies, encounter history, imaging, etc.), \
name the gap explicitly: "I don't have access to lab results in this \
version of the co-pilot — they're visible in the chart's lab section."\
"""

MAX_TOOL_ITERATIONS: Final[int] = 4


class Orchestrator:
    def __init__(
        self,
        llm: LLMClient,
        demographics_fetcher: DemographicsFetcher,
        medications_fetcher: MedicationsFetcher,
        problems_fetcher: ProblemsFetcher,
    ) -> None:
        self._llm = llm
        self._demographics = demographics_fetcher
        self._medications = medications_fetcher
        self._problems = problems_fetcher

    async def turn(self, ctx: RequestContext, user_message: str) -> str:
        """Run one user turn through the model + tools, return final text."""
        messages: list[Message] = [Message(role="user", content=user_message)]
        tools = [
            DEMOGRAPHICS_TOOL_SPEC,
            PROBLEMS_TOOL_SPEC,
            MEDICATIONS_TOOL_SPEC,
        ]

        for _ in range(MAX_TOOL_ITERATIONS):
            response = await self._llm.complete(
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=tools,
                max_tokens=1024,
            )

            messages.append(
                Message(
                    role="assistant",
                    content=response.text,
                    tool_calls=response.tool_calls if response.tool_calls else None,
                )
            )

            if response.stop_reason != "tool_use" or not response.tool_calls:
                return response.text or "(no response)"

            for call in response.tool_calls:
                tool_result_content = await self._dispatch(ctx, call.name)
                messages.append(
                    Message(
                        role="tool",
                        tool_call_id=call.id,
                        content=tool_result_content,
                    )
                )

        return "(orchestrator hit max tool iterations without a final answer)"

    async def _dispatch(self, ctx: RequestContext, tool_name: str) -> str:
        """Run one tool call, return its JSON-stringified result for the model."""
        try:
            if tool_name == "get_demographics":
                result = await self._demographics.fetch(
                    patient_id=ctx.patient_id, raw_token=ctx.raw_token
                )
                return result.model_dump_json()
            if tool_name == "get_active_medications":
                meds = await self._medications.fetch(
                    patient_id=ctx.patient_id, raw_token=ctx.raw_token
                )
                return meds.model_dump_json()
            if tool_name == "get_active_problems":
                probs = await self._problems.fetch(
                    patient_id=ctx.patient_id, raw_token=ctx.raw_token
                )
                return probs.model_dump_json()
        except Exception as exc:
            # Surface tool errors back to the model as structured payloads
            # rather than letting them crash the turn — the model can then
            # tell the user honestly that the lookup failed.
            return json.dumps(
                {"error": "tool_fetch_failed", "tool": tool_name, "detail": str(exc)}
            )

        return json.dumps({"error": "tool_not_implemented", "tool": tool_name})
