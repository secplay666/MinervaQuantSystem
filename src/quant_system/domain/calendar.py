from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from datetime import date


class TradingCalendar:
    """Ordered exchange sessions; the whole calendar is public in advance."""

    def __init__(self, sessions: Sequence[date]) -> None:
        self.sessions: tuple[date, ...] = tuple(sorted(set(sessions)))
        if not self.sessions:
            raise ValueError("empty trading calendar")

    def __len__(self) -> int:
        return len(self.sessions)

    def index(self, day: date) -> int:
        position = bisect_left(self.sessions, day)
        if position >= len(self.sessions) or self.sessions[position] != day:
            raise KeyError(f"{day} is not a session")
        return position

    def index_on_or_after(self, day: date) -> int:
        return bisect_left(self.sessions, day)

    def index_on_or_before(self, day: date) -> int:
        return bisect_right(self.sessions, day) - 1

    def is_month_end(self, index: int) -> bool:
        """Last session of its calendar month (needs the next session)."""
        if index + 1 >= len(self.sessions):
            return True
        current, following = self.sessions[index], self.sessions[index + 1]
        return (current.year, current.month) != (following.year, following.month)
