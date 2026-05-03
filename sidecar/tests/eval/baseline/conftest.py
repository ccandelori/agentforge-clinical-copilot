"""Pytest fixture wiring for the baseline eval suite.

Reuses the live-stack fixtures from ``tests/integration/conftest.py``
(``authenticated_client``, ``patient_context_factory``,
``_wait_for_openemr``, etc.). The baseline cases hit the same live
stack the UC integration tests do, so duplicating the auth + session
wiring would diverge over time and silently drift.

We re-export by importing each fixture function and re-binding under
its original name. ``pytest_plugins`` would also work but pytest's
discovery makes per-package re-exports more debuggable: a missing
fixture surfaces an ImportError here, not a "fixture not found"
deeper in the run.
"""

from __future__ import annotations

# The integration conftest is the canonical home for these fixtures;
# re-exporting them keeps a single source of truth. ``noqa: F401``
# tells ruff that "unused import" is wrong — pytest finds these by
# name in the conftest module namespace.
from tests.integration.conftest import (  # noqa: F401
    _HEALTH_POLL_INTERVAL_SECONDS,  # used transitively
    _HEALTH_POLL_TIMEOUT_SECONDS,
    _HTTP_TIMEOUT,
    _ssl_context_unverified,
    _wait_for_openemr,
    authenticated_client,
    demo_patient_ids,
    openemr_base_url,
    openemr_credentials,
    openemr_session,
    patient_context,
    patient_context_factory,
)
