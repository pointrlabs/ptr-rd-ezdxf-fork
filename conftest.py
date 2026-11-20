#  Copyright (c) 2026, Pointr
#  License: MIT License
"""Pointr fork: the upstream tests the fork fails on purpose are strict xfails,
listed with their reasons in pointr_upstream_deviations.txt."""
from pathlib import Path

import pytest

_LIST = Path(__file__).parent / "pointr_upstream_deviations.txt"


def _deviations() -> set[str]:
    lines = (line.strip() for line in _LIST.read_text().splitlines())
    return {line for line in lines if line and not line.startswith("#")}


def pytest_collection_modifyitems(config, items):
    deviations = _deviations()
    root = Path(str(config.rootpath))
    seen = set()
    for item in items:
        node_id = Path(str(item.path)).relative_to(root).as_posix() + item.nodeid[len(item.nodeid.split("::")[0]):]
        if node_id in deviations:
            seen.add(node_id)
            item.add_marker(pytest.mark.xfail(
                reason="Pointr fork deviation, see pointr_upstream_deviations.txt", strict=True))
    collected_files = {Path(str(i.path)).relative_to(root).as_posix() for i in items}
    stale = {d for d in deviations - seen if d.split("::")[0] in collected_files}
    if stale:
        raise pytest.UsageError(f"pointr_upstream_deviations.txt lists tests that do not exist: {sorted(stale)}")
