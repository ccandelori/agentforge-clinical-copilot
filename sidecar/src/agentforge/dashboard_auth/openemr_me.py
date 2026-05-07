"""OpenEMR ``/me`` identity-bootstrap client for the dashboard auth bridge.

Background: ``docs/adr/0001-dashboard-auth-bridging.md`` §5 lays out
why the dashboard's OIDC session — which only carries ``sub`` and a
``Practitioner/<uuid>`` URI — has to be resolved into the integer
``users.id`` + ``username`` + primary GACL group before we can mint an
internal AGENTFORGE_JWT for the agent's existing pipeline.

This client:

  1. Mints a "lookup-purpose" JWT (correct signature + issuer + short
     ``exp``, no user/patient claims — those are exactly what we don't
     know yet).
  2. ``GET``s ``/interface/.../internal/me.php?user_uuid=<uuid>`` with
     that JWT in the ``Authorization`` header.
  3. Returns an :class:`OpenEMRIdentity` on success or raises an
     :class:`OpenEMRMeFetchError` (with the upstream status code, or 0
     when the request never reached OpenEMR — same convention as
     :class:`agentforge.tools.document_bytes.DocumentBytesFetchError`).

It is the dashboard-side bridge's *single* network call into OpenEMR
for identity. The result should be cached on the session so a turn
doesn't pay a second round-trip.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx
import jwt

ME_PATH = (
    "/interface/modules/custom_modules/oe-module-agentforge/public/internal/me.php"
)
JWT_ISSUER = "openemr-agentforge"
LOOKUP_TOKEN_TTL_SECONDS = 60


class _ClockProto(Protocol):
    def now(self) -> object: ...  # returns datetime; loosened for ducktyping


@dataclass(frozen=True)
class OpenEMRIdentity:
    """Resolved OpenEMR identity for a dashboard session.

    The ``role`` field can be ``None`` for accounts with no GACL group
    membership (the OpenEMR endpoint returns ``"role": null`` in that
    case). The internal-JWT minter handles a missing role by emitting
    a sentinel value the auth-gateway sensitivity policy understands.
    """

    user_id: int
    username: str
    role: str | None


class OpenEMRMeFetchError(RuntimeError):
    """Raised when the ``/me`` lookup fails for any reason.

    ``status_code == 0`` means the request never reached OpenEMR
    (transport-level failure). Non-zero values are the literal HTTP
    status returned by the endpoint.
    """

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class OpenEMRMeFetcher:
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

    async def fetch(self, *, user_uuid: str) -> OpenEMRIdentity:
        token = self._mint_lookup_token()
        url = f"{self._base_url}{ME_PATH}"
        try:
            response = await self._http.get(
                url,
                params={"user_uuid": user_uuid},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise OpenEMRMeFetchError(
                f"OpenEMR /me unreachable: {exc}",
                status_code=0,
            ) from exc

        if response.status_code != 200:
            raise OpenEMRMeFetchError(
                f"OpenEMR /me returned HTTP {response.status_code}",
                status_code=response.status_code,
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise OpenEMRMeFetchError(
                "OpenEMR /me returned non-JSON body",
                status_code=response.status_code,
            ) from exc

        return _parse_identity(body, status_code=response.status_code)

    def _mint_lookup_token(self) -> str:
        now = self._clock.now()
        # Allow datetime or float-timestamp clocks; PyJWT expects either
        # int seconds or datetime in the ``iat``/``exp`` claims.
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


def _parse_identity(body: object, *, status_code: int) -> OpenEMRIdentity:
    if not isinstance(body, dict):
        raise OpenEMRMeFetchError(
            "OpenEMR /me body was not a JSON object",
            status_code=status_code,
        )
    user_id = body.get("user_id")
    username = body.get("username")
    role = body.get("role")
    if not isinstance(user_id, int):
        raise OpenEMRMeFetchError(
            "OpenEMR /me body missing integer user_id",
            status_code=status_code,
        )
    if not isinstance(username, str) or username == "":
        raise OpenEMRMeFetchError(
            "OpenEMR /me body missing username",
            status_code=status_code,
        )
    if role is not None and not isinstance(role, str):
        raise OpenEMRMeFetchError(
            "OpenEMR /me body has unexpected role type",
            status_code=status_code,
        )
    return OpenEMRIdentity(user_id=user_id, username=username, role=role)
