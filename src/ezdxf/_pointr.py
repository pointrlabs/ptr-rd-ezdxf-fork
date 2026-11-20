#  Copyright (c) 2026, Pointr
#  License: MIT License
"""Pointr fork helpers.

The fork skips entities that cannot be drawn or measured instead of failing the
whole drawing, but a silent skip hides bugs: the bounding box bug that skipped
every POLYFACE mesh lived unnoticed behind a debug log. The first skip of each
(place, entity type, error type) in a process is logged as a warning; the
following ones as debug messages.
"""
from __future__ import annotations
import logging
import threading

_seen: set[tuple[str, str, str]] = set()
_lock = threading.Lock()


def log_skipped_entity(
    logger: logging.Logger, where: str, entity, ex: BaseException
) -> None:
    try:
        dxftype = entity.dxftype()
    except Exception:
        dxftype = type(entity).__name__
    key = (where, dxftype, type(ex).__name__)
    with _lock:
        first = key not in _seen
        _seen.add(key)
    if first:
        logger.warning(
            f"{where}: skipped {entity} ({type(ex).__name__}: {ex}); further "
            f"{dxftype} entities skipped by {type(ex).__name__} are logged at debug level"
        )
    else:
        logger.debug(f"{where}: skipped {entity}: {ex}")
