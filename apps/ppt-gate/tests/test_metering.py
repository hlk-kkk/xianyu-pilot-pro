from __future__ import annotations

from app import metering


def test_classify_presenton_actions() -> None:
    assert metering.classify("POST", "/api/v1/ppt/presentation/generate").kind == "generation"
    async_action = metering.classify("POST", "/api/v1/ppt/presentation/generate/async")
    assert async_action is not None
    assert async_action.async_task is True
    assert metering.classify("POST", "/api/v1/ppt/presentation/edit").kind == "ai_edit"
    assert metering.classify("POST", "/api/v1/ppt/chat/message").kind == "ai_edit"
    assert metering.classify("GET", "/api/v1/ppt/images/generate").kind == "image"
    assert metering.classify("GET", "/api/v1/ppt/presentation/all") is None
    assert metering.classify("PATCH", "/api/v1/ppt/presentation/update") is None


def test_free_write_paths() -> None:
    assert metering.is_free_write("PATCH", "/api/v1/ppt/presentation/update")
    assert metering.is_free_write("POST", "/api/v1/ppt/presentation/00000000-0000-0000-0000-000000000001/export")
    assert metering.is_free_write("GET", "/api/v1/ppt/presentation/all")
    assert not metering.is_free_write("POST", "/api/v1/ppt/presentation/generate")


def test_extract_slide_count() -> None:
    assert metering.extract_n_slides(b'{"n_slides": 8}') == 8
    assert metering.extract_n_slides(b'{"slides_markdown": ["a", "b"]}') == 2
    assert metering.extract_n_slides(b'{"content": "x"}') is None
    assert metering.extract_n_slides(b"not-json") is None


def test_task_id_and_request_key() -> None:
    assert metering.task_id_from_response(b'{"id": "task-1"}') == "task-1"
    assert metering.task_id_from_response(b'{"data": {"id": "task-2"}}') == "task-2"
    assert metering.task_id_from_response(b"{}") is None
    assert metering.request_key({"idempotency-key": "abc"}) == "abc"
