"""Error tracking: Sentry when SENTRY_DSN is set and sentry-sdk is installed; otherwise off.

Every event passes through the log scrubber first, so keys and tokens never leave the server.
"""

import logging
import os

from growthcrew.safety import scrub

logger = logging.getLogger(__name__)


def _scrub_event(event: dict, _hint: dict) -> dict:
    import json

    return json.loads(scrub(json.dumps(event, default=str)))


def init_error_tracking() -> bool:
    dsn = os.getenv("SENTRY_DSN")
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed (uv sync --extra ops)")
        return False
    sentry_sdk.init(dsn=dsn, before_send=_scrub_event, send_default_pii=False,
                    traces_sample_rate=float(os.getenv("SENTRY_TRACES_RATE", "0")))  # fmt: skip
    return True
