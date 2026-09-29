"""Direct unit tests for AutoCompact class methods in isolation."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.autocompact import AutoCompact
from nanobot.events import NO_EVENTS, ContextCompactionEvent, EventSink
from nanobot.session.manager import Session, SessionManager


def _runtime(_session: Session | None = None):
    return SimpleNamespace(context_window_tokens=128_000)


def _make_session(
    key: str = "cli:test",
    messages: list | None = None,
    last_archived: int = 0,
    updated_at: datetime | None = None,
    metadata: dict | None = None,
) -> Session:
    """Create a Session with sensible defaults for testing."""
    session = Session(
        key=key,
        messages=messages or [],
        metadata=metadata or {},
    )
    session.last_archived = last_archived
    if updated_at is not None:
        session.updated_at = updated_at
    return session


def _make_autocompact(
    ttl: int = 15,
    sessions: SessionManager | None = None,
    consolidator: MagicMock | None = None,
) -> AutoCompact:
    """Create an AutoCompact with mock dependencies."""
    if sessions is None:
        sessions = MagicMock(spec=SessionManager)
    if consolidator is None:
        consolidator = MagicMock()
        consolidator.compact_idle_session = AsyncMock(return_value="Summary.")
        # Over half of the 128k window: idle sessions qualify unless a test
        # lowers the estimate to exercise the size gate.
        consolidator.estimate_session_prompt_tokens = MagicMock(return_value=(100_000, "test"))
    return AutoCompact(
        sessions=sessions,
        consolidator=consolidator,
        session_ttl_minutes=ttl,
    )


def _add_turns(session: Session, turns: int, *, prefix: str = "msg") -> None:
    """Append simple user/assistant turns to a session."""
    for i in range(turns):
        session.add_message("user", f"{prefix} user {i}")
        session.add_message("assistant", f"{prefix} assistant {i}")


def test_default_ttl_disables_idle_compaction():
    sessions = MagicMock(spec=SessionManager)
    sessions.list_sessions.return_value = [
        {"key": "cli:idle", "updated_at": datetime.now() - timedelta(days=365)},
    ]
    sessions.get_or_create.return_value = _make_session(
        key="cli:idle", messages=[{"role": "user", "content": "pending"}],
    )
    ac = AutoCompact(sessions=sessions, consolidator=MagicMock())
    schedule = MagicMock(side_effect=lambda pending: pending.close())

    ac.check_expired(schedule, _runtime)

    assert schedule.call_count == 0


# ---------------------------------------------------------------------------
# _is_expired
# ---------------------------------------------------------------------------


class TestIsExpired:
    """Test AutoCompact._is_expired edge cases."""

    def test_ttl_zero_always_false(self):
        """TTL=0 means auto-compact is disabled; always returns False."""
        ac = _make_autocompact(ttl=0)
        old = datetime.now() - timedelta(days=365)
        assert ac._is_expired(old) is False

    def test_none_timestamp_returns_false(self):
        """None timestamp should return False."""
        ac = _make_autocompact(ttl=15)
        assert ac._is_expired(None) is False

    def test_empty_string_timestamp_returns_false(self):
        """Empty string timestamp should return False (falsy)."""
        ac = _make_autocompact(ttl=15)
        assert ac._is_expired("") is False

    def test_exactly_at_boundary_is_expired(self):
        """Timestamp exactly at TTL boundary should be expired (>=)."""
        ac = _make_autocompact(ttl=15)
        now = datetime(2026, 1, 1, 12, 0, 0)
        ts = now - timedelta(minutes=15)
        assert ac._is_expired(ts, now=now) is True

    def test_just_under_boundary_not_expired(self):
        """Timestamp just under TTL boundary should NOT be expired."""
        ac = _make_autocompact(ttl=15)
        now = datetime(2026, 1, 1, 12, 0, 0)
        ts = now - timedelta(minutes=14, seconds=59)
        assert ac._is_expired(ts, now=now) is False

    def test_iso_string_parses_correctly(self):
        """ISO format string timestamp should be parsed and evaluated."""
        ac = _make_autocompact(ttl=15)
        now = datetime(2026, 1, 1, 12, 0, 0)
        ts = (now - timedelta(minutes=20)).isoformat()
        assert ac._is_expired(ts, now=now) is True

    def test_custom_now_parameter(self):
        """Custom 'now' parameter should override datetime.now()."""
        ac = _make_autocompact(ttl=10)
        ts = datetime(2026, 1, 1, 10, 0, 0)
        # 9 minutes later → not expired
        now_under = datetime(2026, 1, 1, 10, 9, 0)
        assert ac._is_expired(ts, now=now_under) is False
        # 10 minutes later → expired
        now_over = datetime(2026, 1, 1, 10, 10, 0)
        assert ac._is_expired(ts, now=now_over) is True

    def test_unparseable_string_timestamp_returns_false(self):
        """A persisted timestamp that no longer parses must not raise.

        list_sessions() forwards the raw persisted updated_at string, and
        SessionManager._load already tolerates a malformed value through its
        recovery path. The idle scan must mirror that tolerance instead of crashing.
        """
        ac = _make_autocompact(ttl=15)
        assert ac._is_expired("not-a-timestamp") is False

    def test_tz_aware_string_timestamp_is_compared_by_instant(self):
        """A valid timestamp with an offset remains eligible for expiry."""
        ac = _make_autocompact(ttl=15)
        now = datetime(2026, 1, 1, 12, 0, 0)
        recent = (now - timedelta(minutes=10)).astimezone().isoformat()
        expired = (now - timedelta(minutes=20)).astimezone().isoformat()

        assert ac._is_expired(recent, now=now) is False
        assert ac._is_expired(expired, now=now) is True


# ---------------------------------------------------------------------------
# check_expired
# ---------------------------------------------------------------------------


class TestCheckExpired:
    """Test AutoCompact.check_expired scheduling logic."""

    def test_empty_sessions_list(self):
        """No sessions → schedule_background should never be called."""
        ac = _make_autocompact(ttl=15)
        mock_sm = MagicMock(spec=SessionManager)
        mock_sm.list_sessions.return_value = []
        ac.sessions = mock_sm
        scheduler = MagicMock()
        ac.check_expired(scheduler, _runtime)
        scheduler.assert_not_called()

    def test_expired_session_schedules_background(self):
        """Expired session should trigger schedule_background."""
        ac = _make_autocompact(ttl=15)
        mock_sm = MagicMock(spec=SessionManager)
        old_dt = datetime.now() - timedelta(minutes=20)
        session = _make_session("cli:old", updated_at=old_dt)
        _add_turns(session, 5)
        mock_sm.list_sessions.return_value = [{"key": "cli:old", "updated_at": old_dt.isoformat()}]
        mock_sm.get_or_create.return_value = session
        ac.sessions = mock_sm

        scheduled = []

        def scheduler(coro):
            scheduled.append(coro)
            coro.close()

        ac.check_expired(scheduler, _runtime)
        assert len(scheduled) == 1
        assert "cli:old" in ac._archiving

    def test_unparseable_updated_at_does_not_stop_scan(self):
        """A malformed timestamp is skipped without hiding later sessions.

        The idle scan runs from the agent loop's inbound-timeout branch, so a
        raised exception here would tear down the loop. list_sessions() forwards
        the raw string, so check_expired must tolerate it like SessionManager
        does when loading.
        """
        ac = _make_autocompact(ttl=15)
        mock_sm = MagicMock(spec=SessionManager)
        old_dt = datetime.now() - timedelta(minutes=20)
        session = _make_session("cli:old", updated_at=old_dt)
        _add_turns(session, 5)
        mock_sm.list_sessions.return_value = [
            {"key": "cli:corrupt", "updated_at": "not-a-timestamp"},
            {"key": "cli:old", "updated_at": old_dt.isoformat()},
        ]
        mock_sm.get_or_create.return_value = session
        ac.sessions = mock_sm
        scheduled = []

        def scheduler(coro):
            scheduled.append(coro)
            coro.close()

        ac.check_expired(scheduler, _runtime)

        assert len(scheduled) == 1
        assert ac._archiving == {"cli:old"}

    @pytest.mark.asyncio
    async def test_runtime_is_captured_before_background_starts(self):
        ac = _make_autocompact(ttl=15)
        old_dt = datetime.now() - timedelta(minutes=20)
        session = _make_session("cli:old", updated_at=old_dt)
        _add_turns(session, 5)
        ac.sessions.list_sessions.return_value = [
            {"key": "cli:old", "updated_at": old_dt.isoformat()}
        ]
        ac.sessions.get_or_create.return_value = session
        admitted = _runtime()
        replacement = _runtime()
        resolve_runtime = MagicMock(return_value=admitted)
        scheduled = []

        ac.check_expired(scheduled.append, resolve_runtime)
        resolve_runtime.return_value = replacement
        await scheduled[0]

        resolve_runtime.assert_called_once_with(session)
        ac.consolidator.compact_idle_session.assert_awaited_once_with(
            "cli:old",
            runtime=admitted,
            events=NO_EVENTS,
        )

    @pytest.mark.parametrize("resolution_error", [KeyError, ValueError])
    def test_invalid_preset_is_isolated_to_one_session(self, resolution_error):
        ac = _make_autocompact(ttl=15)
        old_dt = datetime.now() - timedelta(minutes=20)
        sessions = {
            key: _make_session(key, updated_at=old_dt)
            for key in ("cli:removed", "cli:healthy")
        }
        for session in sessions.values():
            _add_turns(session, 5)
        ac.sessions.list_sessions.return_value = [
            {"key": key, "updated_at": old_dt.isoformat()}
            for key in sessions
        ]
        ac.sessions.get_or_create.side_effect = sessions.__getitem__
        healthy_runtime = _runtime()

        def resolve_runtime(session: Session):
            if session.key == "cli:removed":
                raise resolution_error("model preset cannot be resolved")
            return healthy_runtime

        scheduled = []

        def scheduler(coro):
            scheduled.append(coro)
            coro.close()

        ac.check_expired(scheduler, resolve_runtime)

        assert len(scheduled) == 1
        assert ac._archiving == {"cli:healthy"}

    def test_unexpected_runtime_resolution_failure_propagates(self):
        ac = _make_autocompact(ttl=15)
        old_dt = datetime.now() - timedelta(minutes=20)
        session = _make_session("cli:old", updated_at=old_dt)
        _add_turns(session, 5)
        ac.sessions.list_sessions.return_value = [
            {"key": session.key, "updated_at": old_dt.isoformat()}
        ]
        ac.sessions.get_or_create.return_value = session

        def fail(_session: Session):
            raise RuntimeError("unexpected resolver failure")

        with pytest.raises(RuntimeError, match="unexpected resolver failure"):
            ac.check_expired(MagicMock(), fail)

    def test_active_session_key_skips(self):
        """Session in active_session_keys should be skipped."""
        ac = _make_autocompact(ttl=15)
        mock_sm = MagicMock(spec=SessionManager)
        old_ts = (datetime.now() - timedelta(minutes=20)).isoformat()
        mock_sm.list_sessions.return_value = [{"key": "cli:busy", "updated_at": old_ts}]
        ac.sessions = mock_sm
        scheduler = MagicMock()
        ac.check_expired(scheduler, _runtime, active_session_keys={"cli:busy"})
        scheduler.assert_not_called()

    def test_session_already_in_archiving_skips(self):
        """Session already in _archiving set should be skipped."""
        ac = _make_autocompact(ttl=15)
        mock_sm = MagicMock(spec=SessionManager)
        old_ts = (datetime.now() - timedelta(minutes=20)).isoformat()
        mock_sm.list_sessions.return_value = [{"key": "cli:dup", "updated_at": old_ts}]
        ac.sessions = mock_sm
        ac._archiving.add("cli:dup")
        scheduler = MagicMock()
        ac.check_expired(scheduler, _runtime)
        scheduler.assert_not_called()

    def test_session_with_no_key_skips(self):
        """Session info with empty/missing key should be skipped."""
        ac = _make_autocompact(ttl=15)
        mock_sm = MagicMock(spec=SessionManager)
        mock_sm.list_sessions.return_value = [{"key": "", "updated_at": "old"}]
        ac.sessions = mock_sm
        scheduler = MagicMock()
        ac.check_expired(scheduler, _runtime)
        scheduler.assert_not_called()

    def test_session_with_missing_key_field_skips(self):
        """Session info dict without 'key' field should be skipped."""
        ac = _make_autocompact(ttl=15)
        mock_sm = MagicMock(spec=SessionManager)
        mock_sm.list_sessions.return_value = [{"updated_at": "old"}]
        ac.sessions = mock_sm
        scheduler = MagicMock()
        ac.check_expired(scheduler, _runtime)
        scheduler.assert_not_called()

    def test_short_unarchived_session_schedules(self):
        """A short idle session above the size threshold schedules an archive."""
        ac = _make_autocompact(ttl=15)
        mock_sm = MagicMock(spec=SessionManager)
        last_active = datetime(2026, 1, 1, 10, 0, 0)
        session = _make_session("cli:short", updated_at=last_active)
        _add_turns(session, 2)
        mock_sm.list_sessions.return_value = [
            {"key": "cli:short", "updated_at": last_active.isoformat()},
        ]
        mock_sm.get_or_create.return_value = session
        ac.sessions = mock_sm

        scheduled = []

        def scheduler(coro):
            scheduled.append(coro)
            coro.close()

        ac.check_expired(scheduler, _runtime)

        assert len(scheduled) == 1
        assert ac._archiving == {"cli:short"}

    def test_session_under_size_threshold_skips(self):
        """An idle session below half the window must not spend a compact call."""
        ac = _make_autocompact(ttl=15)
        ac.consolidator.estimate_session_prompt_tokens = MagicMock(return_value=(1_000, "test"))
        mock_sm = MagicMock(spec=SessionManager)
        last_active = datetime(2026, 1, 1, 10, 0, 0)
        session = _make_session("cli:light", updated_at=last_active)
        _add_turns(session, 5)
        mock_sm.list_sessions.return_value = [
            {"key": "cli:light", "updated_at": last_active.isoformat()},
        ]
        mock_sm.get_or_create.return_value = session
        ac.sessions = mock_sm

        scheduler = MagicMock()
        ac.check_expired(scheduler, _runtime)

        scheduler.assert_not_called()
        assert ac._archiving == set()

    def test_fully_archived_session_skips(self):
        ac = _make_autocompact(ttl=15)
        mock_sm = MagicMock(spec=SessionManager)
        last_active = datetime(2026, 1, 1, 10, 0, 0)
        session = _make_session("cli:done", updated_at=last_active)
        _add_turns(session, 2)
        session.last_archived = len(session.messages)
        mock_sm.list_sessions.return_value = [
            {"key": "cli:done", "updated_at": last_active.isoformat()},
        ]
        mock_sm.get_or_create.return_value = session
        ac.sessions = mock_sm

        scheduler = MagicMock()
        ac.check_expired(scheduler, _runtime)

        scheduler.assert_not_called()


# ---------------------------------------------------------------------------
# _archive
# ---------------------------------------------------------------------------


class TestArchiveDelegates:
    """_archive should delegate all session mutation to Consolidator."""

    @pytest.mark.asyncio
    async def test_calls_compact_idle_session(self):
        ac = _make_autocompact()
        mock_sm = MagicMock(spec=SessionManager)
        ac.sessions = mock_sm
        ac.consolidator.compact_idle_session = AsyncMock(return_value="Summary.")

        runtime = _runtime()
        await ac._archive("cli:test", runtime=runtime)

        ac.consolidator.compact_idle_session.assert_awaited_once_with(
            "cli:test",
            runtime=runtime,
            events=NO_EVENTS,
        )

    @pytest.mark.asyncio
    async def test_forwards_timeout_compaction_events_with_session_key(self):
        sessions = MagicMock(spec=SessionManager)
        consolidator = MagicMock()
        observed: list[tuple[str, ContextCompactionEvent]] = []

        def bind(key: str):
            async def publish(event: ContextCompactionEvent) -> None:
                observed.append((key, event))
            return EventSink(publish)

        async def compact(key: str, **kwargs):
            event = ContextCompactionEvent(compaction_id="compact-1", phase="started")
            await kwargs["events"].emit(event)
            return "Summary."

        consolidator.compact_idle_session = AsyncMock(side_effect=compact)
        ac = AutoCompact(
            sessions=sessions,
            consolidator=consolidator,
            session_ttl_minutes=15,
            bind_events=bind,
        )

        await ac._archive("cli:test", runtime=_runtime())

        assert len(observed) == 1
        assert observed[0][0] == "cli:test"
        assert observed[0][1].phase == "started"

    @pytest.mark.asyncio
    async def test_archive_context_is_not_reused_for_later_turns(self):
        """_archive delegates to Consolidator and keeps no prompt summary."""
        ac = _make_autocompact()
        mock_sm = MagicMock(spec=SessionManager)
        session = _make_session(
            metadata={"_last_summary": {"text": "Hello.", "last_active": "2026-05-13T10:00:00"}}
        )
        mock_sm.get_or_create.return_value = session
        ac.sessions = mock_sm
        ac.consolidator.compact_idle_session = AsyncMock(return_value="Hello.")

        await ac._archive("cli:test", runtime=_runtime())

        _, summary = ac.prepare_session(session, "cli:test")
        assert summary is None

    @pytest.mark.asyncio
    async def test_exception_still_removes_from_archiving(self):
        ac = _make_autocompact()
        mock_sm = MagicMock(spec=SessionManager)
        ac.sessions = mock_sm
        ac.consolidator.compact_idle_session = AsyncMock(side_effect=RuntimeError("fail"))

        ac._archiving.add("cli:test")
        await ac._archive("cli:test", runtime=_runtime())

        assert "cli:test" not in ac._archiving


# ---------------------------------------------------------------------------
# prepare_session
# ---------------------------------------------------------------------------


class TestPrepareSession:
    """Test AutoCompact.prepare_session logic."""

    def test_key_in_archiving_reloads_session(self):
        """If key is in _archiving, session should be reloaded via get_or_create."""
        ac = _make_autocompact()
        mock_sm = MagicMock(spec=SessionManager)
        reloaded = _make_session(key="cli:test")
        mock_sm.get_or_create.return_value = reloaded
        ac.sessions = mock_sm
        ac._archiving.add("cli:test")

        original_session = _make_session()
        result_session, summary = ac.prepare_session(original_session, "cli:test")

        mock_sm.get_or_create.assert_called_once_with("cli:test")
        assert result_session is reloaded

    def test_expired_session_reloads(self):
        """If session is expired, it should be reloaded via get_or_create."""
        ac = _make_autocompact(ttl=15)
        mock_sm = MagicMock(spec=SessionManager)
        reloaded = _make_session(key="cli:test", updated_at=datetime.now())
        mock_sm.get_or_create.return_value = reloaded
        ac.sessions = mock_sm

        old_session = _make_session(updated_at=datetime.now() - timedelta(minutes=20))
        result_session, summary = ac.prepare_session(old_session, "cli:test")

        mock_sm.get_or_create.assert_called_once_with("cli:test")
        assert result_session is reloaded

    def test_persisted_summary_is_not_reinjected(self):
        """A _last_summary in metadata must never be handed to a later turn."""
        ac = _make_autocompact()
        session = _make_session(metadata={
            "_last_summary": {
                "text": "Cold summary.",
                "last_active": datetime(2026, 5, 13, 14, 0, 0).isoformat(),
            },
        })

        result_session, summary = ac.prepare_session(session, "cli:test")

        assert result_session is session
        assert summary is None

    def test_no_summary_available_returns_none(self):
        """When no summary is available, should return (session, None)."""
        ac = _make_autocompact()
        session = _make_session()

        result_session, summary = ac.prepare_session(session, "cli:test")

        assert result_session is session
        assert summary is None
