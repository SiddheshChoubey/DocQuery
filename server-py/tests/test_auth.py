import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.auth import CONFIG_MESSAGE, ClerkAuth
from app.main import create_app
from tests.conftest import ISSUER, ORIGIN

SIGN_IN = {"message": "Sign in to continue."}
NOT_VERIFIED = {"message": "Your session could not be verified. Sign in again."}


@pytest.fixture
def client(auth):
    app = create_app(auth=auth)

    @app.get("/test/owner")
    def owner(owner_id: str = Depends(app.state.auth)):
        return {"ownerId": owner_id}

    return TestClient(app, raise_server_exceptions=False)


def get_owner(client, token=None, header=None):
    headers = {"Origin": ORIGIN}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if header:
        headers["Authorization"] = header
    return client.get("/test/owner", headers=headers)


def test_valid_session_returns_owner(client, make_token):
    response = get_owner(client, make_token("user_a"))
    assert response.status_code == 200
    assert response.json() == {"ownerId": "user_a"}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("header", [None, "", "Bearer", "Bearer ", "Token abc", "Bearer a b"])
def test_missing_or_malformed_header_asks_to_sign_in(client, header):
    response = client.get("/test/owner", headers={"Authorization": header} if header is not None else {})
    assert response.status_code == 401
    assert response.json() == SIGN_IN


def test_bearer_prefix_is_case_insensitive(client, make_token):
    assert get_owner(client, header=f"bearer {make_token()}").status_code == 200


@pytest.mark.parametrize("overrides", [
    {"sub": "org_123"},                        # not a user
    {"sub": None},
    {"sid": None},                             # not a session token
    {"sid": "client_123"},
    {"iss": "https://other.clerk.accounts.dev"},
    {"azp": "http://evil.example"},            # issued to another frontend
    {"azp": None},
    {"exp": 1},                                # expired
])
def test_rejected_claims(client, make_token, overrides):
    response = get_owner(client, make_token(**overrides))
    assert response.status_code == 401
    assert response.json() == NOT_VERIFIED


def test_rejects_token_signed_with_another_key(client, make_token, other_private_key):
    response = get_owner(client, make_token(key=other_private_key))
    assert response.status_code == 401
    assert response.json() == NOT_VERIFIED


def test_rejects_garbage_token(client):
    response = get_owner(client, "not-a-jwt")
    assert response.status_code == 401
    assert response.json() == NOT_VERIFIED


def test_auth_errors_keep_cors_and_no_store_headers(client):
    response = get_owner(client)
    assert response.status_code == 401
    assert response.headers.get("access-control-allow-origin") == ORIGIN
    assert response.headers["cache-control"] == "no-store"


def test_health_needs_no_token(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "cache-control" not in response.headers


@pytest.mark.parametrize("settings", [
    {"issuer": ISSUER, "authorized_parties": [ORIGIN]},                         # no key
    {"secret_key": "sk_test", "authorized_parties": [ORIGIN]},                  # no issuer
    {"secret_key": "sk_test", "issuer": ISSUER, "authorized_parties": []},      # no origins
])
def test_missing_configuration_stops_startup(settings):
    with pytest.raises(RuntimeError, match="CLERK_SECRET_KEY"):
        ClerkAuth(**settings)
    assert "server-py/.env" in CONFIG_MESSAGE


def test_jwt_key_from_env_accepts_escaped_newlines(monkeypatch, signing_keys, make_token):
    monkeypatch.setenv("CLERK_JWT_KEY", signing_keys[1].replace("\n", "\\n"))
    monkeypatch.setenv("CLERK_ISSUER", ISSUER)
    auth = ClerkAuth.from_env([ORIGIN])
    app = create_app(auth=auth)

    @app.get("/test/owner")
    def owner(owner_id: str = Depends(app.state.auth)):
        return {"ownerId": owner_id}

    assert get_owner(TestClient(app), make_token("user_b")).json() == {"ownerId": "user_b"}


def test_secret_key_mode_fetches_clerk_signing_keys(monkeypatch, signing_keys, make_token):
    calls = {}

    class FakeJWKClient:
        def __init__(self, url, cache_keys, headers):
            calls.update(url=url, cache_keys=cache_keys, headers=headers)

        def get_signing_key_from_jwt(self, _token):
            return type("Key", (), {"key": signing_keys[1]})

    monkeypatch.setattr("app.auth.PyJWKClient", FakeJWKClient)
    auth = ClerkAuth(secret_key="sk_test_123", issuer=ISSUER, authorized_parties=[ORIGIN])
    app = create_app(auth=auth)

    @app.get("/test/owner")
    def owner(owner_id: str = Depends(app.state.auth)):
        return {"ownerId": owner_id}

    assert get_owner(TestClient(app), make_token("user_c")).json() == {"ownerId": "user_c"}
    assert calls == {"url": "https://api.clerk.com/v1/jwks", "cache_keys": True,
                     "headers": {"Authorization": "Bearer sk_test_123"}}
