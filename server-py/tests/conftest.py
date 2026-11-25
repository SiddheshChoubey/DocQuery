import os
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Importing app.main builds the module-level app, which needs Clerk settings.
# Real values from server-py/.env take precedence; tests never contact Clerk.
os.environ.setdefault("CLERK_SECRET_KEY", "sk_test_placeholder")
os.environ.setdefault("CLERK_ISSUER", "https://example.clerk.accounts.dev")
os.environ.setdefault("CLIENT_ORIGINS", "http://localhost:3000")

ISSUER = "https://test-instance.clerk.accounts.dev"
ORIGIN = "http://localhost:3000"


def _key_pair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return private, public_pem


@pytest.fixture(scope="session")
def signing_keys():
    return _key_pair()


@pytest.fixture(scope="session")
def other_private_key():
    return _key_pair()[0]


@pytest.fixture
def make_token(signing_keys):
    """Create a Clerk-style session token signed with the test key."""

    def build(owner="user_a", key=None, **overrides):
        now = int(time.time())
        claims = {"sub": owner, "sid": "sess_test", "iss": ISSUER, "azp": ORIGIN,
                  "iat": now, "nbf": now, "exp": now + 60}
        claims.update(overrides)
        claims = {name: value for name, value in claims.items() if value is not None}
        return jwt.encode(claims, key or signing_keys[0], algorithm="RS256")

    return build


@pytest.fixture
def auth(signing_keys):
    from app.auth import ClerkAuth

    return ClerkAuth(jwt_key=signing_keys[1], issuer=ISSUER, authorized_parties=[ORIGIN])
