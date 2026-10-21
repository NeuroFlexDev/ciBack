"""Run separately from API and generation: python -m app.workers.outbox."""
import logging
import signal
import time
import tempfile
from pathlib import Path
from app.database.db import SessionLocal
import app.models  # Register all models without creating tables.
from app.services.outbox_service import process_one

running = True


def stop(*_):
    global running
    running = False


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while running:
        try:
            (Path(tempfile.gettempdir()) / "lernium-outbox-heartbeat").touch()
            with SessionLocal() as db:
                processed = process_one(db)
            if not processed:
                time.sleep(2)
        except Exception:
            logging.getLogger(__name__).error("Outbox worker iteration failed; retrying in 5 seconds")
            time.sleep(5)


if __name__ == "__main__":
    main()
