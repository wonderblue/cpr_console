"""Membership changes for the Shah live-console watchlist.

Filter-only refreshes move the baseline without emitting enter/leave events.
Quote refreshes compare the current match list with that baseline.
"""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple


def watchlist_membership(
    current_symbols: Iterable[str],
    previous_symbols: Sequence[str] | None,
    *,
    record_events: bool,
) -> Tuple[List[str], List[str], List[str]]:
    """Return ``(entered, exited, baseline)``.

    When ``record_events`` is false the baseline becomes the current list and
    no symbols are reported as entered or left. That is a filter-only tick.
    """
    current = [symbol for symbol in current_symbols if symbol]
    if not record_events:
        return [], [], list(current)
    previous = [symbol for symbol in (previous_symbols or []) if symbol]
    if not previous:
        return [], [], list(current)
    entered = sorted(set(current) - set(previous))
    exited = sorted(set(previous) - set(current))
    return entered, exited, list(current)
