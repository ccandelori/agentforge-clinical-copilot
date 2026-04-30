# AgentForge Sidecar

FastAPI sidecar service for the AgentForge Clinical Co-Pilot. Implements the
Auth Gateway, LangGraph orchestrator, tool layer, streaming verifier, and LLM
client abstraction described in [`../ARCHITECTURE.md`](../ARCHITECTURE.md).

## Local development

Requires [`uv`](https://github.com/astral-sh/uv) and Python 3.12.

```bash
cd sidecar
uv sync                                      # install deps + create .venv
uv run uvicorn agentforge.main:create_app --factory --reload  # run dev server
uv run pytest                                # run tests
uv run mypy --strict src/                    # type check
uv run ruff check                            # lint
```

## Project layout

```
src/agentforge/
├── gateway/         # JWT verification, sensitivity policy, auth gateway
├── orchestrator/    # LangGraph multi-agent orchestration
├── tools/           # Typed adapters over OpenEMR FHIR R4 + internal endpoints
├── verifier/        # Streaming claim-by-claim grounding verifier
├── llm/             # Provider-agnostic LLM client (Claude / OpenAI / vLLM)
└── observability/   # Langfuse integration with HMAC-keyed pseudonyms
```

See `ARCHITECTURE.md` for the full system topology and design decisions.
