"""Tests for telegram-delay-channel-cloner main module."""

import os
import sys
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Set required environment variables for module import."""
    monkeypatch.setenv('BOT_TOKEN', 'fake-bot-token')
    monkeypatch.setenv('SOURCE_CHANNEL', '-1001111111111')
    monkeypatch.setenv('TARGET_CHANNEL', '-1002222222222')
    monkeypatch.setenv('DELAY', '60')
    monkeypatch.setenv('POLLING', '5')
    monkeypatch.setenv('DB_LOCATION', '/tmp/test_messages.db')
    monkeypatch.setenv('COPY_MESSAGE', 'True')
    monkeypatch.setenv('RETENTION_PERIOD', '7')
    monkeypatch.setenv('BATCH_SIZE', '10')


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Create a temporary SQLite database with messages table."""
    db_path = str(tmp_path / "messages.db")
    monkeypatch.setenv('DB_LOCATION', db_path)
    # Re-import to pick up new env
    if 'main' in sys.modules:
        del sys.modules['main']
    import main
    main.DB_LOCATION = db_path
    main.init_db()
    return db_path


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

class TestInitDb:
    def test_creates_messages_table(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "init_test.db")
        monkeypatch.setenv('DB_LOCATION', db_path)
        if 'main' in sys.modules:
            del sys.modules['main']
        import main
        main.DB_LOCATION = db_path
        main.init_db()

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='messages'")
        assert cursor.fetchone() is not None
        conn.close()

    def test_creates_index(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "index_test.db")
        monkeypatch.setenv('DB_LOCATION', db_path)
        if 'main' in sys.modules:
            del sys.modules['main']
        import main
        main.DB_LOCATION = db_path
        main.init_db()

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_forward_time'")
        assert cursor.fetchone() is not None
        conn.close()

    def test_idempotent(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "idempotent_test.db")
        monkeypatch.setenv('DB_LOCATION', db_path)
        if 'main' in sys.modules:
            del sys.modules['main']
        import main
        main.DB_LOCATION = db_path
        main.init_db()
        main.init_db()  # Should not raise


# ---------------------------------------------------------------------------
# update_message_status
# ---------------------------------------------------------------------------

class TestUpdateMessageStatus:
    def test_updates_status(self, temp_db):
        import main
        # Insert a test message
        conn = sqlite3.connect(temp_db)
        conn.execute("INSERT INTO messages (message_id, status, forward_time) VALUES (100, 'to_forward', ?)",
                     (datetime.now().isoformat(),))
        conn.commit()
        conn.close()

        main.update_message_status(100, 'forwarded', 200)

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT status, target_message_id FROM messages WHERE message_id = 100")
        row = cursor.fetchone()
        conn.close()
        assert row[0] == 'forwarded'
        assert row[1] == 200

    def test_updates_without_target_id(self, temp_db):
        import main
        conn = sqlite3.connect(temp_db)
        conn.execute("INSERT INTO messages (message_id, status, forward_time) VALUES (101, 'to_forward', ?)",
                     (datetime.now().isoformat(),))
        conn.commit()
        conn.close()

        main.update_message_status(101, 'failed')

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT status, target_message_id FROM messages WHERE message_id = 101")
        row = cursor.fetchone()
        conn.close()
        assert row[0] == 'failed'
        assert row[1] is None


# ---------------------------------------------------------------------------
# delete_message_from_db
# ---------------------------------------------------------------------------

class TestDeleteMessageFromDb:
    def test_deletes_existing_message(self, temp_db):
        import main
        conn = sqlite3.connect(temp_db)
        conn.execute("INSERT INTO messages (message_id, status, forward_time) VALUES (200, 'to_forward', ?)",
                     (datetime.now().isoformat(),))
        conn.commit()
        conn.close()

        main.delete_message_from_db(200)

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM messages WHERE message_id = 200")
        assert cursor.fetchone() is None
        conn.close()

    def test_no_error_on_nonexistent_message(self, temp_db):
        import main
        # Should not raise
        main.delete_message_from_db(999)


# ---------------------------------------------------------------------------
# channel_post_handler (async)
# ---------------------------------------------------------------------------

class TestChannelPostHandler:
    @pytest.mark.asyncio
    async def test_stores_message_in_db(self, temp_db):
        import main

        mock_update = MagicMock()
        mock_update.channel_post.message_id = 300
        mock_context = MagicMock()

        await main.channel_post_handler(mock_update, mock_context)

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT message_id, status FROM messages WHERE message_id = 300")
        row = cursor.fetchone()
        conn.close()
        assert row is not None
        assert row[1] == 'to_forward'

    @pytest.mark.asyncio
    async def test_ignores_none_post(self, temp_db):
        import main

        mock_update = MagicMock()
        mock_update.channel_post = None
        mock_context = MagicMock()

        await main.channel_post_handler(mock_update, mock_context)
        # Should return without error

    @pytest.mark.asyncio
    async def test_forward_time_is_in_future(self, temp_db):
        import main

        mock_update = MagicMock()
        mock_update.channel_post.message_id = 301
        mock_context = MagicMock()

        before = datetime.now()
        await main.channel_post_handler(mock_update, mock_context)

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT forward_time FROM messages WHERE message_id = 301")
        row = cursor.fetchone()
        conn.close()
        forward_time = datetime.fromisoformat(str(row[0]))
        assert forward_time > before


# ---------------------------------------------------------------------------
# forward_or_copy_message (async)
# ---------------------------------------------------------------------------

class TestForwardOrCopyMessage:
    @pytest.mark.asyncio
    async def test_forwards_ready_messages(self, temp_db):
        import main

        # Insert a message ready to forward (forward_time in past) using string format
        past_time = (datetime.now() - timedelta(seconds=120)).strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(temp_db)
        conn.execute("INSERT INTO messages (message_id, status, forward_time) VALUES (400, 'to_forward', ?)",
                     (past_time,))
        conn.commit()
        conn.close()

        mock_context = MagicMock()
        mock_sent = MagicMock()
        mock_sent.message_id = 401
        mock_context.bot.copy_message = AsyncMock(return_value=mock_sent)

        main.COPY_MESSAGE = True
        await main.forward_or_copy_message(mock_context)

        mock_context.bot.copy_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_future_messages(self, temp_db):
        import main

        # Insert a message not yet ready (forward_time in future)
        future_time = (datetime.now() + timedelta(hours=1)).isoformat()
        conn = sqlite3.connect(temp_db)
        conn.execute("INSERT INTO messages (message_id, status, forward_time) VALUES (500, 'to_forward', ?)",
                     (future_time,))
        conn.commit()
        conn.close()

        mock_context = MagicMock()
        mock_context.bot.copy_message = AsyncMock()

        await main.forward_or_copy_message(mock_context)

        mock_context.bot.copy_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_messages_does_nothing(self, temp_db):
        import main
        mock_context = MagicMock()
        mock_context.bot.copy_message = AsyncMock()

        await main.forward_or_copy_message(mock_context)
        mock_context.bot.copy_message.assert_not_called()


# ---------------------------------------------------------------------------
# delete_old_messages (async)
# ---------------------------------------------------------------------------

class TestDeleteOldMessages:
    @pytest.mark.asyncio
    async def test_deletes_old_records(self, temp_db):
        import main

        # Insert an old message
        old_time = (datetime.now() - timedelta(days=30)).isoformat()
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO messages (message_id, status, forward_time, created_at) VALUES (600, 'forwarded', ?, ?)",
            (old_time, old_time))
        conn.commit()
        conn.close()

        mock_context = MagicMock()
        main.RETENTION_PERIOD = 7
        await main.delete_old_messages(mock_context)

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM messages WHERE message_id = 600")
        assert cursor.fetchone() is None
        conn.close()

    @pytest.mark.asyncio
    async def test_keeps_recent_records(self, temp_db):
        import main

        # Insert a recent message
        recent_time = datetime.now().isoformat()
        conn = sqlite3.connect(temp_db)
        conn.execute(
            "INSERT INTO messages (message_id, status, forward_time, created_at) VALUES (700, 'forwarded', ?, ?)",
            (recent_time, recent_time))
        conn.commit()
        conn.close()

        mock_context = MagicMock()
        main.RETENTION_PERIOD = 7
        await main.delete_old_messages(mock_context)

        conn = sqlite3.connect(temp_db)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM messages WHERE message_id = 700")
        assert cursor.fetchone() is not None
        conn.close()


# ---------------------------------------------------------------------------
# start command (async)
# ---------------------------------------------------------------------------

class TestStartCommand:
    @pytest.mark.asyncio
    async def test_start_replies(self):
        import main
        mock_update = MagicMock()
        mock_update.message.reply_text = AsyncMock()
        mock_context = MagicMock()

        await main.start(mock_update, mock_context)
        mock_update.message.reply_text.assert_called_once_with("Bot started successfully!")


# ---------------------------------------------------------------------------
# Configuration values
# ---------------------------------------------------------------------------

class TestConfiguration:
    def test_delay_is_integer(self):
        import main
        assert isinstance(main.DELAY, int)

    def test_polling_is_integer(self):
        import main
        assert isinstance(main.POLLING, int)

    def test_retention_period_is_integer(self):
        import main
        assert isinstance(main.RETENTION_PERIOD, int)

    def test_batch_size_is_integer(self):
        import main
        assert isinstance(main.BATCH_SIZE, int)
