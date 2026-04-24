import os
import sqlite3
import tempfile
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import BadRequest

import src.main as main


@pytest.fixture(autouse=True)
def tmp_db(tmp_path):
    """Use a temporary file-based SQLite DB so multiple connections share state."""
    db_path = str(tmp_path / "test_messages.db")
    with patch.object(main, "DB_LOCATION", db_path):
        main.init_db()
        yield db_path


def _insert_message(db_path, message_id, status="to_forward", forward_time=None, created_at=None):
    """Helper to insert a test message directly into the DB."""
    if forward_time is None:
        forward_time = datetime.now() - timedelta(seconds=60)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    if created_at:
        cursor.execute(
            "INSERT INTO messages (message_id, status, forward_time, created_at) VALUES (?, ?, ?, ?)",
            (message_id, status, forward_time, created_at),
        )
    else:
        cursor.execute(
            "INSERT INTO messages (message_id, status, forward_time) VALUES (?, ?, ?)",
            (message_id, status, forward_time),
        )
    conn.commit()
    conn.close()


def _get_message(db_path, message_id):
    """Helper to read a message from the DB."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM messages WHERE message_id=?", (message_id,))
    row = cursor.fetchone()
    conn.close()
    return row


def _count_messages(db_path):
    """Helper to count all messages in the DB."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM messages")
    count = cursor.fetchone()[0]
    conn.close()
    return count


# --- init_db ---

class TestInitDb:
    def test_creates_messages_table(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
        assert cursor.fetchone() is not None
        conn.close()

    def test_creates_forward_time_index(self, tmp_db):
        conn = sqlite3.connect(tmp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_forward_time'")
        assert cursor.fetchone() is not None
        conn.close()

    def test_is_idempotent(self, tmp_db):
        with patch.object(main, "DB_LOCATION", tmp_db):
            main.init_db()
            main.init_db()
        conn = sqlite3.connect(tmp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
        assert cursor.fetchone() is not None
        conn.close()


# --- update_message_status ---

class TestUpdateMessageStatus:
    def test_updates_status_and_target_message_id(self, tmp_db):
        _insert_message(tmp_db, 100)
        with patch.object(main, "DB_LOCATION", tmp_db):
            main.update_message_status(100, "forwarded", target_message_id=200)
        row = _get_message(tmp_db, 100)
        assert row[1] == "forwarded"
        assert row[4] == 200

    def test_updates_status_without_target_message_id(self, tmp_db):
        _insert_message(tmp_db, 101)
        with patch.object(main, "DB_LOCATION", tmp_db):
            main.update_message_status(101, "forwarded")
        row = _get_message(tmp_db, 101)
        assert row[1] == "forwarded"
        assert row[4] is None


# --- delete_message_from_db ---

class TestDeleteMessageFromDb:
    def test_deletes_existing_message(self, tmp_db):
        _insert_message(tmp_db, 300)
        with patch.object(main, "DB_LOCATION", tmp_db):
            main.delete_message_from_db(300)
        assert _get_message(tmp_db, 300) is None

    def test_logs_when_message_deleted(self, tmp_db):
        _insert_message(tmp_db, 301)
        with patch.object(main, "DB_LOCATION", tmp_db), \
             patch.object(main.logger, "info") as mock_log:
            main.delete_message_from_db(301)
        mock_log.assert_called_once_with("Removed message 301 from database.")

    def test_does_not_log_when_message_not_found(self, tmp_db):
        with patch.object(main, "DB_LOCATION", tmp_db), \
             patch.object(main.logger, "info") as mock_log:
            main.delete_message_from_db(999)
        mock_log.assert_not_called()


# --- delete_old_messages ---

class TestDeleteOldMessages:
    @pytest.mark.asyncio
    async def test_deletes_messages_older_than_retention_period(self, tmp_db):
        old_time = datetime.now() - timedelta(days=30)
        _insert_message(tmp_db, 400, created_at=old_time)
        _insert_message(tmp_db, 401)  # Recent, should remain
        with patch.object(main, "DB_LOCATION", tmp_db):
            await main.delete_old_messages(MagicMock())
        assert _get_message(tmp_db, 400) is None
        assert _get_message(tmp_db, 401) is not None

    @pytest.mark.asyncio
    async def test_keeps_recent_messages(self, tmp_db):
        _insert_message(tmp_db, 402)
        with patch.object(main, "DB_LOCATION", tmp_db):
            await main.delete_old_messages(MagicMock())
        assert _get_message(tmp_db, 402) is not None


# --- channel_post_handler ---

class TestChannelPostHandler:
    @pytest.mark.asyncio
    async def test_returns_early_when_post_is_none(self, tmp_db):
        update = MagicMock()
        update.channel_post = None
        with patch.object(main, "DB_LOCATION", tmp_db):
            await main.channel_post_handler(update, MagicMock())
        assert _count_messages(tmp_db) == 0

    @pytest.mark.asyncio
    async def test_inserts_message_into_db(self, tmp_db):
        update = MagicMock()
        update.channel_post.message_id = 500
        with patch.object(main, "DB_LOCATION", tmp_db):
            await main.channel_post_handler(update, MagicMock())
        row = _get_message(tmp_db, 500)
        assert row is not None
        assert row[1] == "to_forward"

    @pytest.mark.asyncio
    async def test_sets_forward_time_in_future(self, tmp_db):
        update = MagicMock()
        update.channel_post.message_id = 501
        before = datetime.now()
        with patch.object(main, "DB_LOCATION", tmp_db):
            await main.channel_post_handler(update, MagicMock())
        row = _get_message(tmp_db, 501)
        forward_time = datetime.fromisoformat(row[2])
        assert forward_time > before


# --- start ---

class TestStart:
    @pytest.mark.asyncio
    async def test_replies_with_success_message(self):
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        await main.start(update, MagicMock())
        update.message.reply_text.assert_called_once_with("Bot started successfully!")


# --- forward_or_copy_message ---

class TestForwardOrCopyMessage:
    @pytest.mark.asyncio
    async def test_returns_early_when_no_messages_to_forward(self, tmp_db):
        context = MagicMock()
        with patch.object(main, "DB_LOCATION", tmp_db):
            await main.forward_or_copy_message(context)
        context.bot.copy_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_processes_pending_messages(self, tmp_db):
        past_time = datetime.now() - timedelta(seconds=60)
        _insert_message(tmp_db, 600, forward_time=past_time)
        context = MagicMock()
        context.bot.copy_message = AsyncMock(return_value=MagicMock(message_id=700))
        with patch.object(main, "DB_LOCATION", tmp_db), \
             patch.object(main, "COPY_MESSAGE", True):
            await main.forward_or_copy_message(context)
        context.bot.copy_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_does_not_process_future_messages(self, tmp_db):
        future_time = datetime.now() + timedelta(hours=1)
        _insert_message(tmp_db, 601, forward_time=future_time)
        context = MagicMock()
        with patch.object(main, "DB_LOCATION", tmp_db):
            await main.forward_or_copy_message(context)
        context.bot.copy_message.assert_not_called()


# --- forward_or_copy_message_chunk ---

class TestForwardOrCopyMessageChunk:
    @pytest.mark.asyncio
    async def test_copies_message_when_copy_mode_enabled(self, tmp_db):
        _insert_message(tmp_db, 700)
        context = MagicMock()
        context.bot.copy_message = AsyncMock(return_value=MagicMock(message_id=800))
        with patch.object(main, "DB_LOCATION", tmp_db), \
             patch.object(main, "COPY_MESSAGE", True), \
             patch.object(main, "SOURCE_CHANNEL", "-100111"), \
             patch.object(main, "TARGET_CHANNEL", "-100222"):
            await main.forward_or_copy_message_chunk(context, [(700,)])
        context.bot.copy_message.assert_called_once_with(
            chat_id="-100222", from_chat_id="-100111", message_id=700
        )

    @pytest.mark.asyncio
    async def test_forwards_message_when_copy_mode_disabled(self, tmp_db):
        _insert_message(tmp_db, 701)
        context = MagicMock()
        context.bot.forward_message = AsyncMock(return_value=MagicMock(message_id=801))
        with patch.object(main, "DB_LOCATION", tmp_db), \
             patch.object(main, "COPY_MESSAGE", False), \
             patch.object(main, "SOURCE_CHANNEL", "-100111"), \
             patch.object(main, "TARGET_CHANNEL", "-100222"):
            await main.forward_or_copy_message_chunk(context, [(701,)])
        context.bot.forward_message.assert_called_once_with(
            chat_id="-100222", from_chat_id="-100111", message_id=701
        )

    @pytest.mark.asyncio
    async def test_deletes_message_on_bad_request_invalid_id(self, tmp_db):
        _insert_message(tmp_db, 702)
        context = MagicMock()
        context.bot.copy_message = AsyncMock(side_effect=BadRequest("message_id_invalid"))
        with patch.object(main, "DB_LOCATION", tmp_db), \
             patch.object(main, "COPY_MESSAGE", True), \
             patch.object(main.logger, "error") as mock_log:
            await main.forward_or_copy_message_chunk(context, [(702,)])
        mock_log.assert_called_once()
        assert "message_id_invalid" in mock_log.call_args[0][0]
        assert _get_message(tmp_db, 702) is None

    @pytest.mark.asyncio
    async def test_logs_error_on_other_bad_request(self, tmp_db):
        _insert_message(tmp_db, 703)
        context = MagicMock()
        context.bot.copy_message = AsyncMock(side_effect=BadRequest("chat not found"))
        with patch.object(main, "DB_LOCATION", tmp_db), \
             patch.object(main, "COPY_MESSAGE", True), \
             patch.object(main.logger, "error") as mock_log:
            await main.forward_or_copy_message_chunk(context, [(703,)])
        mock_log.assert_called_once()
        assert "chat not found" in mock_log.call_args[0][0]

    @pytest.mark.asyncio
    async def test_logs_error_on_unexpected_exception(self, tmp_db):
        _insert_message(tmp_db, 704)
        context = MagicMock()
        context.bot.copy_message = AsyncMock(side_effect=RuntimeError("network down"))
        with patch.object(main, "DB_LOCATION", tmp_db), \
             patch.object(main, "COPY_MESSAGE", True), \
             patch.object(main.logger, "error") as mock_log:
            await main.forward_or_copy_message_chunk(context, [(704,)])
        mock_log.assert_called_once()
        assert "network down" in mock_log.call_args[0][0]

    @pytest.mark.asyncio
    async def test_finally_block_deletes_message_after_success(self, tmp_db):
        _insert_message(tmp_db, 705)
        context = MagicMock()
        context.bot.copy_message = AsyncMock(return_value=MagicMock(message_id=805))
        with patch.object(main, "DB_LOCATION", tmp_db), \
             patch.object(main, "COPY_MESSAGE", True):
            await main.forward_or_copy_message_chunk(context, [(705,)])
        assert _get_message(tmp_db, 705) is None

    @pytest.mark.asyncio
    async def test_processes_multiple_messages_in_chunk(self, tmp_db):
        _insert_message(tmp_db, 706)
        _insert_message(tmp_db, 707)
        context = MagicMock()
        context.bot.copy_message = AsyncMock(return_value=MagicMock(message_id=900))
        with patch.object(main, "DB_LOCATION", tmp_db), \
             patch.object(main, "COPY_MESSAGE", True):
            await main.forward_or_copy_message_chunk(context, [(706,), (707,)])
        assert context.bot.copy_message.call_count == 2
        assert _get_message(tmp_db, 706) is None
        assert _get_message(tmp_db, 707) is None
