"""OpenEMR ``/patient_pid`` patient-bootstrap client.

ADR-0001 §5 — pairs with :mod:`agentforge.dashboard_auth.openemr_me`.
The dashboard knows its active patient by FHIR Patient resource UUID;
the agent's internal JWT carries an integer ``patient_data.pid``.
This client closes that gap.

Mints a lookup-purpose JWT (same shape as the /me bridge) and
``GET``s ``/interface/.../internal/patient_pid.php?patient_uuid=<uuid>``,
returning the integer pid on 200 or raising
:class:`OpenEMRPatientPidFetchError` on any non-200 / transport
failure (status_code=0 indicates the request never reached OpenEMR).
"""

from __future__ import annotations

from typing import Protocol

import httpx
import jwt

PATIENT_PID_PATH = (
    "/interface/modules/custom_modules/oe-module-agentforge/public/internal/patient_pid.php"
)
JWT_ISSUER = "openemr-agentforge"
LOOKUP_TOKEN_TTL_SECONDS = 60


class _ClockProto(Protocol):
    def now(self) -> object: ...


class OpenEMRPatientPidFetchError(RuntimeError):
    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class OpenEMRPatientPidFetcher:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        base_url: str,
        jwt_secret: str,
        clock: _ClockProto,
    ) -> None:
        self._http = http
        self._base_url = base_url.rstrip("/")
        self._jwt_secret = jwt_secret
        self._clock = clock

    async def fetch(self, *, patient_uuid: str) -> int:
        token = self._mint_lookup_token()
        url = f"{self._base_url}{PATIENT_PID_PATH}"
        try:
            response = await self._http.get(
                url,
                params={"patient_uuid": patient_uuid},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise OpenEMRPatientPidFetchError(
                f"OpenEMR /patient_pid unreachable: {exc}",
                status_code=0,
            ) from exc

        if response.status_code != 200:
            raise OpenEMRPatientPidFetchError(
                f"OpenEMR /patient_pid returned HTTP {response.status_code}",
                status_code=response.status_code,
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise OpenEMRPatientPidFetchError(
                "OpenEMR /patient_pid returned non-JSON body",
                status_code=response.status_code,
            ) from exc

        if not isinstance(body, dict):
            raise OpenEMRPatientPidFetchError(
                "OpenEMR /patient_pid body was not a JSON object",
                status_code=response.status_code,
            )
        pid = body.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            raise OpenEMRPatientPidFetchError(
                "OpenEMR /patient_pid body missing positive-int pid",
                status_code=response.status_code,
            )
        return pid

    def _mint_lookup_token(self) -> str:
        now = self._clock.now()
        if hasattr(now, "timestamp"):
            iat = int(now.timestamp())  # type: ignore[attr-defined]
        else:
            iat = int(now)  # pragma: no cover — defensive
        exp = iat + LOOKUP_TOKEN_TTL_SECONDS
        return jwt.encode(
            {"iss": JWT_ISSUER, "iat": iat, "exp": exp},
            self._jwt_secret,
            algorithm="HS256",
        )
