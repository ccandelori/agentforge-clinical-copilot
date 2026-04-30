"""Provider-agnostic LLM client abstraction.

A thin Protocol exposing async streaming with tools. Concrete implementations
(ClaudeClient, OpenAIClient, VLLMClient) are selected by config so agent code
does not depend on any single provider. See ARCHITECTURE.md §5.
"""
