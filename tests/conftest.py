import os

# Set required env vars BEFORE main.py is imported anywhere
os.environ.setdefault("BOT_TOKEN", "fake-token")
os.environ.setdefault("SOURCE_CHANNEL", "-1001234567890")
os.environ.setdefault("TARGET_CHANNEL", "-1009876543210")
os.environ.setdefault("DB_LOCATION", ":memory:")
os.environ.setdefault("DELAY", "10")
os.environ.setdefault("POLLING", "5")
os.environ.setdefault("COPY_MESSAGE", "True")
os.environ.setdefault("RETENTION_PERIOD", "7")
os.environ.setdefault("BATCH_SIZE", "10")
