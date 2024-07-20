import os
import sqlite3
import logging
from datetime import datetime, timedelta, time
from dotenv import load_dotenv
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ApplicationBuilder, CallbackContext, CommandHandler, MessageHandler, filters
from tenacity import retry, wait_exponential, stop_after_attempt

# Load environment variables if using .env
load_dotenv()

# Configuration values from the environment
DB_LOCATION = os.getenv('DB_LOCATION', '/data/messages.db') # Defaults to /data location
SOURCE_CHANNEL = os.getenv('SOURCE_CHANNEL')
TARGET_CHANNEL = os.getenv('TARGET_CHANNEL')
DELAY = int(os.getenv('DELAY', 10)) # Defaults to 10s
POLLING = int(os.getenv('POLLING', 5)) # Defaults to 5s
BOT_TOKEN = os.getenv('BOT_TOKEN')
COPY_MESSAGE = os.getenv('COPY_MESSAGE', True) # Defaults to copying message instead of forwarding
RETENTION_PERIOD = int(os.getenv('RETENTION_PERIOD', 7)) # Defaults to 7 days
BATCH_SIZE = int(os.getenv('BATCH_SIZE', 10)) # Defaults to processing 10 messages at a time

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_db():
    conn = sqlite3.connect(DB_LOCATION)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            message_id INTEGER PRIMARY KEY,
            status TEXT,
            forward_time TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            target_message_id INTEGER
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_forward_time ON messages(forward_time)')
    conn.commit()
    conn.close()

async def forward_or_copy_message_chunk(context: CallbackContext, chunk):
    for row in chunk:
        msg_id = row[0]
        try:
            if COPY_MESSAGE:
                sent_msg = await context.bot.copy_message(chat_id=TARGET_CHANNEL, from_chat_id=SOURCE_CHANNEL, message_id=msg_id)
            else:
                sent_msg = await context.bot.forward_message(chat_id=TARGET_CHANNEL, from_chat_id=SOURCE_CHANNEL, message_id=msg_id)
            update_message_status(msg_id, 'forwarded', sent_msg.message_id)
        except BadRequest as e:
            if 'message_id_invalid' in str(e):
                logger.error(f"Failed to process message {msg_id}: {e} - Message may have been deleted.")
                delete_message_from_db(msg_id)
            else:
                logger.error(f"Failed to process message {msg_id}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error processing message {msg_id}: {e}")
        finally:
            # Ensure message is removed from DB on failure
            delete_message_from_db(msg_id)

async def forward_or_copy_message(context: CallbackContext):
    conn = sqlite3.connect(DB_LOCATION)
    cursor = conn.cursor()
    current_time = datetime.now()

    cursor.execute('SELECT message_id FROM messages WHERE status="to_forward" AND forward_time<=? LIMIT ?', (current_time, BATCH_SIZE))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return

    await forward_or_copy_message_chunk(context, rows)

def update_message_status(message_id, status, target_message_id=None):
    conn = sqlite3.connect(DB_LOCATION)
    cursor = conn.cursor()
    cursor.execute('UPDATE messages SET status=?, target_message_id=? WHERE message_id=?', (status, target_message_id, message_id))
    conn.commit()
    conn.close()

def delete_message_from_db(message_id):
    conn = sqlite3.connect(DB_LOCATION)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM messages WHERE message_id=?', (message_id,))
    if cursor.rowcount > 0:
        logger.info(f"Removed message {message_id} from database.")
    conn.commit()
    conn.close()

async def delete_old_messages(context: CallbackContext):
    conn = sqlite3.connect(DB_LOCATION)
    cursor = conn.cursor()
    cutoff_time = datetime.now() - timedelta(days=RETENTION_PERIOD)

    cursor.execute('DELETE FROM messages WHERE created_at <= ?', (cutoff_time,))
    conn.commit()
    conn.close()

async def channel_post_handler(update: Update, context: CallbackContext):
    post = update.channel_post
    if post is None:
        return

    message_id = post.message_id
    forward_time = datetime.now() + timedelta(seconds=DELAY)
    conn = sqlite3.connect(DB_LOCATION)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO messages (message_id, status, forward_time) VALUES (?, ?, ?)',
                   (message_id, 'to_forward', forward_time))
    conn.commit()
    conn.close()

async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Bot started successfully!")

if __name__ == "__main__":
    init_db()

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Command handlers
    application.add_handler(CommandHandler("start", start))

    # Message handlers
    application.add_handler(MessageHandler(filters.Chat(int(SOURCE_CHANNEL)) & filters.ChatType.CHANNEL, channel_post_handler))

    # Job queue
    job_queue = application.job_queue
    job_queue.run_repeating(forward_or_copy_message, interval=POLLING, first=10)
    job_queue.run_daily(delete_old_messages, time=time(hour=0, minute=0, second=0))  # Run at midnight

    # Start the bot
    application.run_polling()
