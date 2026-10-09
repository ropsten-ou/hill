"""An activity calendar: weeks as columns, weekdays as rows, each day shaded
by how many things happened on it (here: notes changed)."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget

WEEKDAYS = ["Mon", "", "Wed", "", "Fri", "", ""]


class ActivityCalendar(Widget, can_focus=True):
    """Arrow keys move between days, Enter picks one (`DayPicked`). It shows
    `weeks` weeks, up to this one, or as many of them as fit its width."""

    DEFAULT_CSS = """
    ActivityCalendar { height: 8; padding: 0 1; }
    ActivityCalendar > .activity--label { color: $text-muted; }
    ActivityCalendar > .activity--none { color: $foreground 20%; }
    ActivityCalendar > .activity--low { color: $success 45%; }
    ActivityCalendar > .activity--mid { color: $success 75%; }
    ActivityCalendar > .activity--high { color: $success; }
    ActivityCalendar > .activity--cursor { background: $accent; }
    """
    COMPONENT_CLASSES = {
        "activity--label",
        "activity--none",
        "activity--low",
        "activity--mid",
        "activity--high",
        "activity--cursor",
    }
    BINDINGS = [
        Binding("left", "move(-7)", show=False),
        Binding("right", "move(7)", show=False),
        Binding("up", "move(-1)", show=False),
        Binding("down", "move(1)", show=False),
        Binding("enter", "pick", show=False),
    ]

    class DayPicked(Message):
        def __init__(self, day: date) -> None:
            super().__init__()
            self.day = day

    def __init__(self, counts: Counter[date] | None = None, weeks: int = 16, **kwargs) -> None:
        super().__init__(**kwargs)
        self.counts = counts or Counter()
        self.weeks = weeks
        self.cursor = date.today()
        self.picked: date | None = None

    def set_counts(self, counts: Counter[date]) -> None:
        self.counts = counts
        self.refresh()

    def set_weeks(self, weeks: int) -> None:
        self.weeks = weeks
        self.cursor = max(self.cursor, self.first_day)
        self.refresh()

    @property
    def shown(self) -> int:
        """How many weeks show: `weeks`, or as many as fit, a day label and
        two columns a week."""
        width = self.content_size.width
        return min(self.weeks, max(1, (width - 4) // 2)) if width else self.weeks

    @property
    def first_day(self) -> date:
        today = date.today()
        return today - timedelta(days=today.weekday(), weeks=self.shown - 1)

    def on_resize(self) -> None:
        self.cursor = max(self.cursor, self.first_day)

    def _level(self, count: int) -> str:
        if count == 0:
            return "none"
        return "low" if count == 1 else "mid" if count <= 3 else "high"

    def render(self) -> Text:
        first, today = self.first_day, date.today()
        label = self.get_component_rich_style("activity--label")
        cursor = self.get_component_rich_style("activity--cursor")
        text = Text(no_wrap=True)
        # Month names above the week in which each month starts.
        months = [" "] * (4 + 2 * self.shown)
        for week in range(self.shown):
            monday = first + timedelta(weeks=week)
            if week == 0 or monday.month != (monday - timedelta(weeks=1)).month:
                name = monday.strftime("%b")
                col = 4 + 2 * week
                if col + len(name) <= len(months) and all(c == " " for c in months[col : col + len(name)]):
                    months[col : col + len(name)] = list(name)
        text.append("".join(months).rstrip() + "\n", label)
        for weekday in range(7):
            text.append(f"{WEEKDAYS[weekday]:<4}", label)
            for week in range(self.shown):
                day = first + timedelta(weeks=week, days=weekday)
                if day > today:
                    text.append("  ")
                    continue
                style = self.get_component_rich_style(f"activity--{self._level(self.counts[day])}")
                if day == self.cursor and (self.has_focus or day == self.picked):
                    style = style + cursor
                text.append("■", style)
                text.append(" ")
            if weekday < 6:
                text.append("\n")
        return text

    def action_move(self, days: int) -> None:
        day = self.cursor + timedelta(days=days)
        if self.first_day <= day <= date.today():
            self.cursor = day
            self.refresh()

    def action_pick(self) -> None:
        self.picked = self.cursor
        self.post_message(self.DayPicked(self.cursor))
        self.refresh()

    def on_click(self, event: events.Click) -> None:
        week, weekday = (event.x - 1 - 4) // 2, event.y - 1
        day = self.first_day + timedelta(weeks=week, days=weekday)
        if 0 <= week < self.shown and 0 <= weekday < 7 and day <= date.today():
            self.cursor = day
            self.action_pick()

    def on_focus(self) -> None:
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()
