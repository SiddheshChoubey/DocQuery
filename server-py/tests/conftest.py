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
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")

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

    def build(owner="user_a", key=None, headers=None, **overrides):
        now = int(time.time())
        claims = {"sub": owner, "sid": "sess_test", "iss": ISSUER, "azp": ORIGIN,
                  "iat": now, "nbf": now, "exp": now + 60}
        claims.update(overrides)
        claims = {name: value for name, value in claims.items() if value is not None}
        return jwt.encode(claims, key or signing_keys[0], algorithm="RS256", headers=headers)

    return build


@pytest.fixture
def auth(signing_keys):
    from app.auth import ClerkAuth

    return ClerkAuth(jwt_key=signing_keys[1], issuer=ISSUER, authorized_parties=[ORIGIN])


def build_pdf(pages: list[str]) -> bytes:
    """A minimal valid PDF with one line of text per page (no PDF library needed)."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", None,
               "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        content_ref = len(objects)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                       f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_ref} 0 R >>")
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    output, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(output)
    output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    output += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    output += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return output
