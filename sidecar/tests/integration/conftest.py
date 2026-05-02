"""Pytest fixtures for the AgentForge integration test suite.

These tests exercise the full PHP-module-through-sidecar flow against a
running dev-easy + agent stack. They are slow (tens of seconds per
turn) and require live infrastructure, so they're isolated in
``sidecar/tests/integration/`` and the fixtures here gate the whole
directory on a reachable OpenEMR — if the stack isn't up, every
integration test skips cleanly rather than reporting a hard failure.

How to run::

    cd sidecar
    uv run pytest tests/integration/                  # runs them
    uv run pytest                                    # auto-skips them when dev-easy is down

Required upstream:

* ``docker/development-easy`` stack up (``docker compose up --detach
  --wait``) — provides openemr + mysql + phpmyadmin
* ``docker/agent`` stack OR the host-script sidecar — provides the
  agent endpoint at port 8400 (compose) or 8000 (host script)

Defaults assume the dev-easy stack on its standard ports. Override
via environment variables documented in :func:`openemr_base_url`
below.
"""

from __future__ import annotations

import os
import ssl
from collections.abc import AsyncIterator
from typing import Final

import httpx
import pytest

# Reasonable timeouts for live integration:
#   * connect: 5s — dev-easy boots in ~3 minutes; once up, connect
#     is instant on a healthy stack.
#   * read: 30s — most agent /turn calls finish in <15s; bulky
#     synthesis on Eula's chart is the worst-case at ~25s.
_HTTP_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(
    connect=5.0, read=30.0, write=10.0, pool=5.0
)

# Health-probe parameters for wait_for_openemr. dev-easy's openemr
# container typically takes 60-180s to go from "started" to "ready"
# on a cold boot; tighter for warm.
_HEALTH_POLL_INTERVAL_SECONDS: Final[float] = 2.0
_HEALTH_POLL_TIMEOUT_SECONDS: Final[float] = 30.0


@pytest.fixture(scope="session")
def openemr_base_url() -> str:
    """Resolve the OpenEMR base URL.

    Reads ``AGENTFORGE_INT_OPENEMR_URL`` (override for a remote
    droplet or non-default port). Defaults to dev-easy's HTTPS port
    on localhost. Tests connect with SSL verification disabled — the
    dev container ships a self-signed cert and adding a CA bundle
    just for tests is not worth the maintenance burden.
    """
    return os.environ.get(
        "AGENTFORGE_INT_OPENEMR_URL", "https://localhost:9300"
    )


@pytest.fixture(scope="session")
def openemr_credentials() -> tuple[str, str]:
    """Resolve the dev-easy admin credentials.

    Defaults to ``admin``/``pass`` (dev-easy's seeded credentials).
    Override via ``AGENTFORGE_INT_OPENEMR_USER`` and
    ``AGENTFORGE_INT_OPENEMR_PASS`` if testing against a deployment
    where the defaults have been rotated.
    """
    return (
        os.environ.get("AGENTFORGE_INT_OPENEMR_USER", "admin"),
        os.environ.get("AGENTFORGE_INT_OPENEMR_PASS", "pass"),
    )


def _ssl_context_unverified() -> ssl.SSLContext:
    """Build an SSL context that skips verification.

    dev-easy's certificate is a self-signed CA cert with no SAN
    matching ``localhost``. Strict verification fails. We disable
    verification here — these tests run only against trusted local
    dev infrastructure and a configurable remote URL the operator
    explicitly opted into.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


@pytest.fixture(scope="session")
async def _wait_for_openemr(openemr_base_url: str) -> str:
    """Poll OpenEMR until it responds (or skip the suite).

    Session-scoped so a cold-boot dev-easy only blocks once. If the
    stack is genuinely down, every dependent test ``pytest.skip``s
    via this fixture's path — no hard failures.

    Returns the base URL on success so downstream fixtures can
    chain depend on it cleanly.
    """
    deadline = _HEALTH_POLL_TIMEOUT_SECONDS
    elapsed = 0.0
    last_error: str | None = None

    probe_timeout = httpx.Timeout(
        connect=2.0, read=2.0, write=2.0, pool=2.0
    )
    async with httpx.AsyncClient(
        verify=_ssl_context_unverified(), timeout=probe_timeout
    ) as probe:
        while elapsed < deadline:
            try:
                response = await probe.get(f"{openemr_base_url}/")
                # Anything in the 200-499 range means OpenEMR is up
                # enough to respond. 5xx or no response means try again.
                if response.status_code < 500:
                    return openemr_base_url
                last_error = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:  # network-layer failures
                last_error = type(exc).__name__
            import asyncio

            await asyncio.sleep(_HEALTH_POLL_INTERVAL_SECONDS)
            elapsed += _HEALTH_POLL_INTERVAL_SECONDS

    pytest.skip(
        f"OpenEMR not reachable at {openemr_base_url} after "
        f"{deadline}s (last error: {last_error}). Bring up "
        "docker/development-easy and retry."
    )


@pytest.fixture
async def openemr_session(
    _wait_for_openemr: str,
    openemr_credentials: tuple[str, str],
) -> AsyncIterator[httpx.Cookies]:
    """Authenticate against OpenEMR and yield a CookieJar.

    Posts admin/pass (or the configured override) to OpenEMR's
    standard login endpoint and returns the resulting cookies. The
    cookie jar is what authenticates subsequent /turn requests —
    AgentProxyController bridges the OpenEMR session into a JWT
    server-side, so we don't need to touch the JWT directly.

    Login fails are surfaced as ``pytest.fail`` rather than a skip
    because the upstream check already confirmed the stack is up;
    this is a real auth-flow regression worth flagging loudly.
    """
    base_url = _wait_for_openemr
    user, password = openemr_credentials

    async with httpx.AsyncClient(
        verify=_ssl_context_unverified(),
        timeout=_HTTP_TIMEOUT,
        follow_redirects=True,
    ) as client:
        # OpenEMR's login is a POST to main_screen.php with auth=login.
        # The admin form fields are authUser + clearPass; the new_login
        # field signals "this is a fresh interactive login" rather than
        # a session refresh.
        response = await client.post(
            f"{base_url}/interface/main/main_screen.php",
            params={"auth": "login", "site": "default"},
            data={
                "new_login_session_management": "1",
                "authUser": user,
                "clearPass": password,
                "languageChoice": "1",
            },
        )

        # OpenEMR's login renders an HTML page on success; on failure it
        # also renders HTML but with no OpenEMR session cookie. The
        # presence of the OpenEMR cookie is the structural success
        # signal — status code alone is not enough (failed auth still
        # returns 200 with the login form re-rendered).
        if "OpenEMR" not in client.cookies:
            pytest.fail(
                f"OpenEMR login at {base_url} did not set a session "
                f"cookie. Response status was {response.status_code}; "
                f"check the credentials in AGENTFORGE_INT_OPENEMR_USER/"
                "PASS."
            )

        yield client.cookies


@pytest.fixture
async def authenticated_client(
    _wait_for_openemr: str,
    openemr_session: httpx.Cookies,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield an httpx.AsyncClient pre-loaded with the OpenEMR session.

    This is the workhorse for integration tests — pass it to test
    code, get an authenticated client back. Cookies are owned by
    :func:`openemr_session`; this fixture just wires them in.
    """
    async with httpx.AsyncClient(
        base_url=_wait_for_openemr,
        cookies=openemr_session,
        verify=_ssl_context_unverified(),
        timeout=_HTTP_TIMEOUT,
        follow_redirects=False,
    ) as client:
        yield client


# ---------------------------------------------------------------------
# Patient context fixtures (Task 47.2)
#
# These build on the auth fixtures above. To exercise /agentforge/turn
# end-to-end the request needs both a valid OpenEMR session AND a
# patient context — the AgentProxyController returns 400 ("no patient
# context") if $_SESSION['pid'] isn't set. OpenEMR sets pid when the
# user navigates to patient_file/summary/demographics.php?set_pid=N,
# so the integration tests do the same via the authenticated client.
# ---------------------------------------------------------------------

# Default demo patients available in the seeded dataset (see
# scripts/seed/agentforge_demo_overlay.sql). Eula (pid=8) is the
# complex chronic-condition patient; Alena (pid=4) is the sparse
# patient. Tests pick whichever serves their scenario; defaulting
# the list lets the fixtures gate gracefully when the demo seed
# isn't loaded.
DEFAULT_DEMO_PATIENT_IDS: Final[tuple[int, ...]] = (4, 8)


@pytest.fixture(scope="session")
def demo_patient_ids() -> tuple[int, ...]:
    """Patient IDs known to exist in the demo seed.

    Override via ``AGENTFORGE_INT_DEMO_PIDS`` (comma-separated) when
    testing against a deployment with a different cohort.
    """
    raw = os.environ.get("AGENTFORGE_INT_DEMO_PIDS")
    if not raw:
        return DEFAULT_DEMO_PATIENT_IDS
    parsed: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            parsed.append(int(part))
    return tuple(parsed) if parsed else DEFAULT_DEMO_PATIENT_IDS


async def _set_patient_context(
    client: httpx.AsyncClient, pid: int
) -> httpx.Response:
    """Set the OpenEMR session's pid by visiting the demographics page.

    The standard OpenEMR mechanism: any authenticated GET to
    /interface/patient_file/summary/demographics.php?set_pid=N updates
    $_SESSION['pid'] server-side for the rest of the session. The
    response body is the rendered demographics page; we don't inspect
    it here, but the test gets it back so it can verify the patient
    name appears (defense against silently setting the wrong pid).
    """
    return await client.get(
        "/interface/patient_file/summary/demographics.php",
        params={"set_pid": pid},
    )


@pytest.fixture
async def patient_context_factory(
    authenticated_client: httpx.AsyncClient,
):
    """Return an async callable that sets patient context on the session.

    Usage in tests::

        async def test_something(patient_context_factory):
            await patient_context_factory(8)
            # Subsequent /agentforge/turn calls now have pid=8 server-side.

    The factory returns the underlying httpx response so callers can
    sanity-check (e.g. assert the response status was 200 — anything
    else means the patient probably doesn't exist in the seed).
    """

    async def _set(pid: int) -> httpx.Response:
        response = await _set_patient_context(authenticated_client, pid)
        if response.status_code >= 400:
            pytest.skip(
                f"Could not set patient context for pid={pid} "
                f"(status {response.status_code}); is the demo seed "
                "loaded? Run scripts/seed/validate_seed.sh to check."
            )
        return response

    return _set


@pytest.fixture
async def patient_context(
    patient_context_factory,
    demo_patient_ids: tuple[int, ...],
) -> int:
    """Default-context fixture: bind to the first demo patient.

    For tests that don't care which patient as long as ONE patient is
    bound. Returns the pid that was set so the test can refer to it
    in assertions.
    """
    pid = demo_patient_ids[0]
    await patient_context_factory(pid)
    return pid
