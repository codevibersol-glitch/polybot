"""
utils/logger.py
──────────────
Centralised logging setup:
  • Rotating file handler → polybot.log (DEBUG level, 5 MB × 3 backups)
  • GUI queue handler     → pushes (level, message) tuples to a queue that
                            the Logs tab drains every 200 ms (thread-safe)
  • Console handler       → INFO level during development

Usage:
    from utils.logger import get_logger, set_gui_queue
    log = get_logger(__name__)
    log.info("Hello")
"""

import logging
import queue
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ── Module-level queue that the Logs tab subscribes to ────────────────────────
_gui_queue: "queue.Queue | None" = None

# ── Paths ─────────────────────────────────────────────────────────────────────
LOG_FILE = Path(__file__).resolve().parent.parent / "polybot.log"


# ── Custom handler: pushes records to the GUI queue ───────────────────────────
class _QueueHandler(logging.Handler):
    """Non-blocking handler that puts (levelname, message) onto a queue."""

    def __init__(self, q: queue.Queue) -> None:
        super().__init__()
        self._q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            # Push tuple so the GUI can colour-code by level.
            # put_nowait raises queue.Full when the GUI is slow – silently
            # discard rather than printing a traceback every reconnect cycle.
            try:
                self._q.put_nowait((record.levelname, msg))
            except Exception:   # queue.Full or queue gone
                pass
        except Exception:  # noqa: BLE001
            self.handleError(record)


# ── Root logger configuration ─────────────────────────────────────────────────
def _configure_root() -> None:
    """Set up root logger once (idempotent)."""
    root = logging.getLogger()
    if root.handlers:
        return  # Already configured – skip

    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)-30s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler – DEBUG
    fh = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Console handler – INFO
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    root.addHandler(ch)


_configure_root()


# ── Public API ────────────────────────────────────────────────────────────────
def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Call this at module top-level."""
    return logging.getLogger(name)


def set_gui_queue(q: queue.Queue) -> None:
    """
    Register the GUI queue.  Call once from the App __init__ BEFORE
    starting background threads so log messages are captured from the start.
    """
    global _gui_queue
    _gui_queue = q

    root = logging.getLogger()
    handler = _QueueHandler(q)
    handler.setLevel(logging.INFO)  # Only INFO+ in GUI log viewer
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s – %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(handler)
