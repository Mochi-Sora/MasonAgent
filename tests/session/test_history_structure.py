"""Structured history reads: reasoning, tool activity, and file edits."""

from __future__ import annotations

import json
from pathlib import Path

from mason.session import SessionManager
from mason.session.access import SessionAccess
from mason.utils.runtime import (
    BUDGET_EXHAUSTED_FINALIZATION_PROMPT,
    FINALIZATION_RETRY_PROMPT,
    build_budget_exhausted_finalization_message,
    build_finalization_retry_message,
    build_length_recovery_message,
)


def _manager(tmp_path: Path) -> SessionManager:
    return SessionManager(tmp_path / "workspace", sessions_root=tmp_path / "sessions")


def test_history_pairs_reasoning_and_tool_activity(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = manager.get_or_create("telegram:chat")
    session.messages = [
        {"role": "user", "content": "write the note", "timestamp": "2026-10-14T09:00:00"},
        {
            "role": "assistant",
            "content": "On it.",
            "timestamp": "2026-10-14T09:00:01",
            "reasoning_content": "The user wants a file.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"path": "notes/todo.md", "content": "buy milk"}),
                    },
                },
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {"name": "exec", "arguments": json.dumps({"command": "false"})},
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "write_file",
            "content": "Wrote notes/todo.md",
        },
        {"role": "tool", "tool_call_id": "call_2", "name": "exec", "content": "Error: command failed"},
        {"role": "assistant", "content": "Done.", "timestamp": "2026-10-14T09:00:02"},
    ]
    manager.save(session)

    match = SessionAccess(_manager(tmp_path)).read("telegram:chat", query="", limit=10)

    assert match is not None
    messages = match["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant", "assistant"]
    activity = messages[1]
    assert activity["reasoning"] == ["The user wants a file."]
    assert activity["tools"] == [
        {"name": "write_file", "ok": True, "summary": None},
        {"name": "exec", "ok": False, "summary": "Error: command failed"},
    ]
    assert activity["file_edits"] == [{"path": "notes/todo.md", "ok": True}]
    assert messages[2]["reasoning"] == []
    assert messages[2]["tools"] == []


def test_history_falls_back_to_thinking_blocks(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = manager.get_or_create("cli:chat")
    session.messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "hello",
            "thinking_blocks": [
                {"type": "thinking", "thinking": "First thought."},
                {"type": "thinking", "text": "Second thought."},
            ],
        },
    ]
    manager.save(session)

    match = SessionAccess(_manager(tmp_path)).read("cli:chat", query="", limit=10)

    assert match is not None
    assert match["messages"][1]["reasoning"] == ["First thought.", "Second thought."]


def test_tool_only_turns_are_visible_without_text(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = manager.get_or_create("cli:tools")
    session.messages = [
        {"role": "user", "content": "check the files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": json.dumps({"path": "."})},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "name": "list_dir", "content": "a.txt\nb.txt"},
    ]
    manager.save(session)

    match = SessionAccess(_manager(tmp_path)).read("cli:tools", query="", limit=10)

    assert match is not None
    activity = match["messages"][1]
    assert activity["content"] == ""
    assert activity["tools"] == [{"name": "list_dir", "ok": True, "summary": None}]


def test_hidden_continuation_prompt_marks_the_next_assistant(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = manager.get_or_create("cli:long")
    session.messages = [
        {"role": "user", "content": "write a very long answer"},
        {"role": "assistant", "content": "First segment."},
        build_length_recovery_message("First segment."),
        {"role": "assistant", "content": "Second segment continues."},
    ]
    manager.save(session)

    match = SessionAccess(_manager(tmp_path)).read("cli:long", query="", limit=10)

    assert match is not None
    messages = match["messages"]
    assert [message["role"] for message in messages] == ["user", "assistant", "assistant"]
    assert [message["content"] for message in messages] == [
        "write a very long answer",
        "First segment.",
        "Second segment continues.",
    ]
    assert messages[1]["continued"] is False
    assert messages[2]["continued"] is True
    assert not any("cut off" in message["content"] for message in messages)


def test_hidden_retry_prompts_do_not_mark_continuation(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = manager.get_or_create("cli:retry")
    session.messages = [
        {"role": "user", "content": "answer this"},
        build_finalization_retry_message(),
        {"role": "assistant", "content": "The answer."},
        build_budget_exhausted_finalization_message(),
        {"role": "assistant", "content": "The budget answer."},
    ]
    manager.save(session)

    match = SessionAccess(_manager(tmp_path)).read("cli:retry", query="", limit=10)

    assert match is not None
    messages = match["messages"]
    assert [message["content"] for message in messages] == [
        "answer this",
        "The answer.",
        "The budget answer.",
    ]
    assert [message["continued"] for message in messages] == [False, False, False]
    assert not any(
        prompt in message["content"]
        for message in messages
        for prompt in (FINALIZATION_RETRY_PROMPT, BUDGET_EXHAUSTED_FINALIZATION_PROMPT)
    )


def test_attachment_records_hide_path_placeholders(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    record = {
        "attachment_id": "att-0123456789ab",
        "name": "photo.png",
        "mime": "image/png",
        "kind": "image",
        "size": 42,
        "duration_ms": None,
    }
    session = manager.get_or_create("desktop:media")
    session.messages = [
        {
            "role": "user",
            "content": "look at this\n[image: /root/.mason/media/desktop/uuid.png]",
            "media": ["/root/.mason/media/desktop/uuid.png"],
            "attachments": [record],
        },
        {"role": "assistant", "content": "Nice photo."},
        {
            "role": "user",
            "content": "[image: photo.jpg]",
            "attachments": [{**record, "name": "photo.jpg"}],
        },
    ]
    manager.save(session)

    match = SessionAccess(_manager(tmp_path)).read("desktop:media", query="", limit=10)

    assert match is not None
    messages = match["messages"]
    assert messages[0]["content"] == "look at this"
    assert messages[0]["attachments"] == [record]
    assert messages[2]["content"] == ""
    assert messages[2]["attachments"][0]["name"] == "photo.jpg"
    assert "/root/.mason/media" not in json.dumps(messages)
