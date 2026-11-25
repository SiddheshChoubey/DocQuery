"""Clerk session authentication.

Mirrors server/auth.js: a request is accepted only with a Clerk *session* token
for this Clerk instance and this frontend. The token's `sub` (Clerk user id)
becomes the owner id that scopes every document, job, and conversation.
"""

import os
import re
from typing import Callable, Optional

import jwt
from fastapi import Request
from jwt import PyJWKClient

from app.errors import ApiError

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
                 verify: Optional[Callable[[str], dict]] = None):
        if (not secret_key and not jwt_key) or not issuer or not authorized_parties:
            raise RuntimeError(CONFIG_MESSAGE)
        self.secret_key = secret_key
        self.jwt_key = jwt_key
        self.issuer = issuer
        self.authorized_parties = list(authorized_parties)
        self.verify = verify or self._verify_signature
        self._jwks: Optional[PyJWKClient] = None

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

    def _signing_key(self, token: str):
        if self.jwt_key:
            return self.jwt_key
        if self._jwks is None:
            # Keys are cached, so Clerk is contacted only when they are new or rotated.
            self._jwks = PyJWKClient(CLERK_JWKS_URL, cache_keys=True,
                                     headers={"Authorization": f"Bearer {self.secret_key}"})
        return self._jwks.get_signing_key_from_jwt(token).key

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
            subject, session = claims.get("sub"), claims.get("sid")
            # Pin the instance and frontend, and require a session token, not another token type.
            if not (isinstance(subject, str) and subject.startswith("user_")
                    and isinstance(session, str) and session.startswith("sess_")
                    and claims.get("iss") == self.issuer
                    and claims.get("azp") in self.authorized_parties):
                raise ValueError("Invalid session")
        except Exception:
            raise ApiError(401, VERIFY_MESSAGE) from None
        request.state.owner_id = subject
        return subject
