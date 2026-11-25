"""Clerk session authentication.

Mirrors server/auth.js: a request is accepted only with a Clerk *session* token
for this Clerk instance and this frontend. The token's `sub` (Clerk user id)
becomes the owner id that scopes every document, job, and conversation.
"""

import logging
import os
import re
import time
from typing import Callable, Optional

import httpx
import jwt
from fastapi import Request

from app.errors import ApiError

logger = logging.getLogger("docquery.auth")

CLERK_JWKS_URL = "https://api.clerk.com/v1/jwks"
CONFIG_MESSAGE = "Configure CLERK_SECRET_KEY (or CLERK_JWT_KEY), CLERK_ISSUER, and CLIENT_ORIGINS in server-py/.env."
SIGN_IN_MESSAGE = "Sign in to continue."
VERIFY_MESSAGE = "Your session could not be verified. Sign in again."

_BEARER = re.compile(r"^Bearer ([^\s]+)$", re.IGNORECASE)


class ClerkAuth:
    """FastAPI dependency that returns the signed-in user's id.

    `verify` can be replaced in tests; by default tokens are verified locally with
    CLERK_JWT_KEY, or with Clerk's signing keys fetched using CLERK_SECRET_KEY.
    """

    def __init__(self, *, secret_key: Optional[str] = None, jwt_key: Optional[str] = None,
                 issuer: Optional[str] = None, authorized_parties: list[str] = (),
                 verify: Optional[Callable[[str], dict]] = None, http: Optional[httpx.Client] = None):
        if (not secret_key and not jwt_key) or not issuer or not authorized_parties:
            raise RuntimeError(CONFIG_MESSAGE)
        self.secret_key = secret_key
        self.jwt_key = jwt_key
        self.issuer = issuer
        self.authorized_parties = list(authorized_parties)
        self.verify = verify or self._verify_signature
        self.http = http or httpx.Client(timeout=10)
        self._keys: dict = {}
        self._fetched_at = 0.0

    @classmethod
    def from_env(cls, authorized_parties: list[str]) -> "ClerkAuth":
        jwt_key = os.getenv("CLERK_JWT_KEY")
        return cls(
            secret_key=os.getenv("CLERK_SECRET_KEY"),
            # .env files usually store a PEM key on one line with literal "\n".
            jwt_key=jwt_key.replace("\\n", "\n") if jwt_key else None,
            issuer=os.getenv("CLERK_ISSUER"),
            authorized_parties=authorized_parties,
        )

    def _fetch_keys(self) -> None:
        response = self.http.get(CLERK_JWKS_URL, headers={"Authorization": f"Bearer {self.secret_key}",
                                                          "User-Agent": "docquery-api"})
        if not response.is_success:
            raise ValueError(f"Clerk signing keys request failed with status {response.status_code}")
        self._keys = {key.key_id: key.key for key in jwt.PyJWKSet.from_dict(response.json()).keys}
        self._fetched_at = time.monotonic()

    def _signing_key(self, token: str):
        if self.jwt_key:
            return self.jwt_key
        key_id = jwt.get_unverified_header(token).get("kid")
        # Keys are cached; fetch again only for a new key id (key rotation), at most every 5 minutes.
        if not self._keys or (key_id not in self._keys and time.monotonic() - self._fetched_at > 300):
            self._fetch_keys()
        if key_id not in self._keys:
            raise ValueError("Token was signed with an unknown key")
        return self._keys[key_id]

    def _verify_signature(self, token: str) -> dict:
        # Checks the signature plus exp/nbf/iat, allowing 5 seconds of clock skew.
        return jwt.decode(token, self._signing_key(token), algorithms=["RS256"], leeway=5,
                          options={"verify_aud": False, "require": ["exp", "iat", "sub"]})

    def __call__(self, request: Request) -> str:
        match = _BEARER.match(request.headers.get("authorization", ""))
        if not match:
            raise ApiError(401, SIGN_IN_MESSAGE)
        try:
            claims = self.verify(match.group(1))
        except Exception as error:
            # Log why (never the token) so a setup problem is visible in the API terminal.
            logger.warning("Session verification failed: %s: %s", type(error).__name__, error)
            raise ApiError(401, VERIFY_MESSAGE) from None
        subject, session = claims.get("sub"), claims.get("sid")
        # Pin the instance and frontend, and require a session token, not another token type.
        problem = None
        if not (isinstance(subject, str) and subject.startswith("user_")):
            problem = "token is not for a user"
        elif not (isinstance(session, str) and session.startswith("sess_")):
            problem = "token is not a session token"
        elif claims.get("iss") != self.issuer:
            problem = f"issuer {claims.get('iss')!r} does not match CLERK_ISSUER {self.issuer!r}"
        elif claims.get("azp") not in self.authorized_parties:
            problem = f"origin {claims.get('azp')!r} is not in CLIENT_ORIGINS"
        if problem:
            logger.warning("Session verification failed: %s", problem)
            raise ApiError(401, VERIFY_MESSAGE)
        request.state.owner_id = subject
        return subject
