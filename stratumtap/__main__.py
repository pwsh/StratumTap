"""``python -m stratumtap`` entry point."""

from __future__ import annotations

import logging

import uvicorn

from .config import get_settings


def main() -> None:
    """Run the app under uvicorn using the configured host/port/log level."""
    settings = get_settings()
    # uvicorn only configures its own loggers; give ours a handler and level too.
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(levelname)s: %(name)s: %(message)s",
    )
    uvicorn.run(
        "stratumtap.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
