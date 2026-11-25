"""Checks against the real langchain-google-genai package (no network calls)."""

import pytest

pytest.importorskip("langchain_google_genai", reason="install requirements.txt to run Gemini setup tests")

from google.genai.errors import ClientError  # noqa: E402
from langchain_google_genai import ChatGoogleGenerativeAI  # noqa: E402
from langchain_google_genai.chat_models import _handle_client_error  # noqa: E402

from app.chat import DEFAULT_CHAT_MODEL, create_chat_model  # noqa: E402
from app.errors import error_status  # noqa: E402


def test_chat_models_match_the_node_settings(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_CHAT_MODEL", raising=False)
    answer_model = create_chat_model()
    assert isinstance(answer_model, ChatGoogleGenerativeAI)
    assert answer_model.model == DEFAULT_CHAT_MODEL == "gemini-3.5-flash-lite"
    assert answer_model.max_retries == 2
    assert create_chat_model(max_retries=0).max_retries == 0  # Used for query rewriting.


def test_chat_model_can_be_overridden(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_CHAT_MODEL", "gemini-other")
    assert create_chat_model().model == "gemini-other"


def wrapped(code: int, status: str) -> Exception:
    """A Gemini error after LangChain has wrapped it, as the chat service sees it."""
    try:
        _handle_client_error(ClientError(code, {"error": {"code": code, "status": status, "message": "details"}}),
                             {"model": "models/gemini-3.5-flash-lite"})
    except Exception as error:
        return error
    raise AssertionError("LangChain did not raise")


def test_wrapped_quota_errors_are_recognised():
    assert error_status(wrapped(429, "RESOURCE_EXHAUSTED")) == 429


def test_other_wrapped_errors_are_not_quota_errors():
    assert error_status(wrapped(403, "PERMISSION_DENIED")) == 403
    assert error_status(wrapped(400, "INVALID_ARGUMENT")) == 400
