"""Provider-agnostic LLM client abstraction.

A thin Protocol exposing async completion with tool calls. Concrete
implementations (ClaudeClient, OpenAIClient, VLLMClient) are selected by
config so agent code does not depend on any single provider. See
ARCHITECTURE.md §5.
"""

from agentforge.llm.claude import DEFAULT_MODEL, ClaudeClient
from agentforge.llm.client import LLMClient
from agentforge.llm.types import LLMResponse, Message, ToolCall, ToolSpec

__all__ = [
    "DEFAULT_MODEL",
    "ClaudeClient",
    "LLMClient",
    "LLMResponse",
    "Message",
    "ToolCall",
    "ToolSpec",
]
