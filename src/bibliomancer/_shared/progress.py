"""Progress model: turns a job's printed lines into overall / current-step
completion and an ETA. It only reads lines the commands already print, so the
commands stay unaware of the panel.

Conventions both apps' commands follow
  "=== <step label> ==="            a step starts (matched by prefix against PLANS)
  "=== pipeline finished ... ==="   the job is over (any label in FINISH)
  "Report written: <path>"          the panel opens <path> when the job ends

An app subclasses Progress, fills PLANS / COUNT_STEPS / LABELS and implements
parse(line) to call set_total / set_done / item_started / item_finished.
"""

from __future__ import annotations

import re
import time

from .widgets import fmt_dur


class Progress:
    # kind -> [(label prefix, what the user sees, weight)]; weight 0 = set from counts
    PLANS: dict[str, list[tuple[str, str, int]]] = {}
    # step key -> (noun for the caption, weight of one item, default seconds per item)
    COUNT_STEPS: dict[str, tuple[str, float, float]] = {}
    LABELS: dict[str, str] = {}            # caption of single-step jobs by kind
    FINISH = ("pipeline finished",)
    RE_STAMP = re.compile(r"^\[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\]\s*")

    def __init__(self, kind: str, hint: int = 0, label: str = ""):
        self.kind = kind
        plan = self.PLANS.get(kind)
        self.counted = plan is not None
        self.plan = [list(s) for s in plan] if plan else \
            [["", label or self.LABELS.get(kind, "Working"), 1]]
        self.cur = -1 if self.counted else 0
        self.t0 = time.time()
        self.step_t0 = time.time()
        self.n = {k: 0 for k in self.COUNT_STEPS}
        self.done = {k: 0 for k in self.COUNT_STEPS}
        self.item_t0: float | None = None
        self.item_times: list[float] = []
        self.item = self.item_note = ""
        self.ok_items = 0
        self.finished = False
        self.report = ""
        for key in self.COUNT_STEPS:
            self._weight(key, hint)

    # ---------------------------------------------------------- feeding --
    def _weight(self, key: str, n: int) -> None:
        per = self.COUNT_STEPS[key][1]
        for s in self.plan:
            if s[0] == key:
                s[2] = max(1, n * per)

    def set_total(self, key: str, n: int) -> None:
        if key in self.n and n != self.n[key]:
            self.n[key] = n
            self._weight(key, n)

    def set_done(self, key: str, d: int) -> None:
        if key in self.done:
            self.done[key] = d

    def item_started(self, key: str, name: str, i: int | None = None, n: int | None = None):
        """A sequential item begins (enables the smooth partial progress)."""
        if n is not None:
            self.set_total(key, n)
        if i is not None:
            self.done[key] = i - 1
        self.item_t0, self.item, self.item_note = time.time(), name, ""

    def item_finished(self, key: str, seconds: float | None = None, ok: bool = True):
        if self.item_t0 is not None or seconds is not None:
            self.item_times.append(seconds if seconds is not None else time.time() - self.item_t0)
        self.done[key] = self.done.get(key, 0) + 1
        self.ok_items += bool(ok)
        self.item_t0 = None

    def current_key(self) -> str:
        return self.plan[self.cur][0] if 0 <= self.cur < len(self.plan) else ""

    def feed(self, line: str) -> None:
        s = self.RE_STAMP.sub("", line.strip())
        if s.startswith("===") and s.endswith("==="):
            label = s.strip("= ").strip()
            if label.startswith(self.FINISH):
                self.cur = len(self.plan)
            elif self.counted:
                for i, step in enumerate(self.plan):
                    if step[0] and label.startswith(step[0]) and i > self.cur:
                        self.cur, self.item, self.item_note = i, "", ""
                        self.step_t0, self.item_t0 = time.time(), None
                        break
            elif not self.LABELS.get(self.kind) and self.plan[0][1] == "Working":
                self.plan[0][1] = label[:1].upper() + label[1:]
            return
        if s.startswith("Report written: "):
            self.report = s[len("Report written: "):].strip()
            return
        self.parse(s)

    def parse(self, s: str) -> None:
        """App-specific lines (counts, items). Default: nothing."""

    # ---------------------------------------------------------- reading --
    def step_label(self) -> str:
        if self.finished or self.cur >= len(self.plan):
            return "Finished"
        return "Starting" if self.cur < 0 else self.plan[self.cur][1]

    def caption(self, key: str, d: int, n: int, rate_per_min: float | None, eta: str) -> str:
        noun = self.COUNT_STEPS[key][0]
        pm = f" · {rate_per_min:.1f}/min" if rate_per_min else ""
        return f"{noun} {min(d + 1, n)} of {n}{pm}{eta}"

    def step_fraction(self):
        """(fraction or None if unknown, caption) of the current step."""
        if not (0 <= self.cur < len(self.plan)):
            return None, ""
        key = self.plan[self.cur][0]
        if key in self.COUNT_STEPS and self.n.get(key):
            d, n = self.done[key], self.n[key]
            default_s = self.COUNT_STEPS[key][2]
            el = time.time() - self.step_t0
            # sequential items report their own durations; concurrent ones
            # (set_done only) are timed by the step's measured rate
            if self.item_times:
                per = sum(self.item_times) / len(self.item_times)
            else:
                per = el / d if d and el > 0 else default_s
            part = 0.0
            if self.item_t0 is not None and per:
                part = min(0.95, (time.time() - self.item_t0) / per)
            done = min(n, d + part)
            left = (n - done) * per if per else 0
            eta = f" · about {fmt_dur(left)} left" if left > 30 else ""
            rpm = d / el * 60 if d and el > 20 and not self.item_times else None
            return done / n, self.caption(key, d, n, rpm, eta)
        return self.extra_fraction(key)

    def extra_fraction(self, key: str):
        """Hook for steps measured another way (e.g. 'embedded 40/500')."""
        return None, self.plan[self.cur][1] if 0 <= self.cur < len(self.plan) else ""

    def overall(self) -> float:
        if self.finished or self.cur >= len(self.plan):
            return 1.0
        if self.cur < 0:
            return 0.0
        total = sum(s[2] for s in self.plan) or 1
        done = sum(s[2] for s in self.plan[: self.cur])
        f, _ = self.step_fraction()
        return min(0.999, (done + self.plan[self.cur][2] * (f or 0.0)) / total)

    def step_counter(self) -> str:
        if self.finished or not self.counted or not 0 <= self.cur < len(self.plan):
            return ""
        return f"Step {self.cur + 1} of {len(self.plan)}"

    def item_text(self) -> str:
        """Line under the bars: the item being worked on, while a counted step runs."""
        if not self.item or self.current_key() not in self.COUNT_STEPS:
            return ""
        return self.item[:110] + (f"  —  {self.item_note}" if self.item_note else "")

    def summary(self) -> str:
        """'  ·  12 paper(s) read' for the finished caption; app override."""
        return ""
