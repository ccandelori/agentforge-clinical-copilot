"""Minimal MVP orchestrator: single-tool loop, no verifier, no parallel dispatch.

The full ARCHITECTURE.md §3 design has Planner / ToolDispatcher / Synthesizer /
Verifier nodes. This MVP collapses all of that into one tight loop:

  user_message -> Claude -> (tool_use? -> dispatch -> tool_result -> Claude)+ -> text

We rely on Claude's stop_reason to know when to stop tool-calling; we cap the
iteration count so a misbehaving model can't burn the deadline. When more
tools land (Tasks 15-25) and the verifier ships (Task 28), the loop here
gets replaced by a real LangGraph state machine.
"""

from __future__ import annotations

import json
from typing import Final

from agentforge.gateway.auth_gateway import RequestContext
from agentforge.llm.client import LLMClient
from agentforge.llm.types import Message
from agentforge.tools.demographics import DEMOGRAPHICS_TOOL_SPEC, DemographicsFetcher

SYSTEM_PROMPT: Final[str] = (
    "You are AgentForge, a clinical co-pilot embedded inside OpenEMR. "
    "You are answering a clinician about a single patient whose chart is "
    "currently open. Use the get_demographics tool to look up patient "
    "details before you summarize or answer demographic questions. "
    "Be concise, clinical, and non-speculative. Cite the tool result you "
    "drew from. If a tool call fails, say so plainly rather than guessing."
)

MAX_TOOL_ITERATIONS: Final[int] = 3


class Orchestrator:
    def __init__(
        self,
        llm: LLMClient,
        demographics_fetcher: DemographicsFetcher,
    ) -> None:
        self._llm = llm
        self._demographics_fetcher = demographics_fetcher

    async def turn(self, ctx: RequestContext, user_message: str) -> str:
        """Run one user turn through the model + tools, return final text."""
        messages: list[Message] = [Message(role="user", content=user_message)]
        tools = [DEMOGRAPHICS_TOOL_SPEC]

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
        if tool_name == "get_demographics":
            try:
                result = await self._demographics_fetcher.fetch(
                    patient_id=ctx.patient_id,
                    raw_token=ctx.raw_token,
                )
                return result.model_dump_json()
            except Exception as exc:
                # Surface tool errors back to the model as structured payloads
                # rather than letting them crash the turn — the model can then
                # tell the user honestly that the lookup failed.
                return json.dumps({"error": "demographics_fetch_failed", "detail": str(exc)})

        return json.dumps({"error": "tool_not_implemented", "tool": tool_name})
