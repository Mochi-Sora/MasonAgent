"""Runtime log visibility controls shared by CLI commands."""

from loguru import logger

__all__ = ["_set_mason_logs"]


def _set_mason_logs(enabled: bool) -> None:
    if enabled:
        logger.enable("mason")
    else:
        logger.disable("mason")
