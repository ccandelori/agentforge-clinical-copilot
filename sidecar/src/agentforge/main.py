"""FastAPI application entry point for the AgentForge sidecar.

Exposes only the /health endpoint at this stage; the agent turn handler,
auth gateway, orchestrator wiring, and tool routes are added in
subsequent tasks. See ARCHITECTURE.md §1 for the system topology.
"""

from fastapi import FastAPI

from agentforge.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construct the FastAPI application. Factory keeps tests independent."""
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    return app


app = create_app()
