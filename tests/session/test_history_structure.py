"""Structured history reads: reasoning, tool activity, and file edits."""

from __future__ import annotations

import json
from pathlib import Path

from mason.session import SessionManager
from mason.session.access import SessionAccess


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
