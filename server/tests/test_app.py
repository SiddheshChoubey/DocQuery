from fastapi.testclient import TestClient

from app.errors import ApiError
from app.main import create_app


def make_client(auth):
    app = create_app(auth=auth)

    @app.get("/test/api-error")
    def api_error():
        raise ApiError(409, "This document is being deleted.")

    class QuotaError(Exception):
        status = 429

    @app.get("/test/quota")
    def quota():
        raise QuotaError("RESOURCE_EXHAUSTED key=secret")

    @app.get("/test/crash")
    def crash():
        raise RuntimeError("https://provider.example?key=secret")

    return TestClient(app, raise_server_exceptions=False)


def test_health(auth):
    response = make_client(auth).get("/")
    assert response.status_code == 200
    assert response.json() == {"status": "All Good!"}


def test_api_error_uses_message_format(auth):
    response = make_client(auth).get("/test/api-error")
    assert response.status_code == 409
    assert response.json() == {"message": "This document is being deleted."}


def test_quota_error_hides_provider_details(auth):
    response = make_client(auth).get("/test/quota")
    assert response.status_code == 429
    assert response.json() == {"message": "Gemini quota reached. Wait and retry, or check your free-tier limits in AI Studio."}


def test_unexpected_error_hides_details(auth):
    response = make_client(auth).get("/test/crash")
    assert response.status_code == 500
    assert "secret" not in response.text
    assert response.json() == {"message": "Request failed. Check the API key, model access, Qdrant, and Valkey."}


def test_unexpected_error_keeps_cors_headers(auth):
    # Without CORS headers the browser hides the error message from the frontend.
    response = make_client(auth).get("/test/crash", headers={"Origin": "http://localhost:3000"})
    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_cors_allows_frontend_origin(auth):
    response = make_client(auth).options("/", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "DELETE",
        "Access-Control-Request-Headers": "authorization",
    })
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_cors_rejects_other_origin(auth):
    response = make_client(auth).get("/", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in response.headers
