# src/analysis/trace.py
"""
A record of how the universe became today's ticket.

The brief could already show the 49 names, the 8 shortlisted and the 3 bought. It
could not show the arrows between them -- and the biggest cut, 45 down to 8, left
no trace at all, so there was no way to ask "why isn't BBRI in my list?".

**The one rule this module exists to enforce: the trail is written by the code that
makes the decision, never reconstructed by the code that displays it.** A view that
re-derives the rules will eventually disagree with them, and an explanation that is
confidently wrong is worse than no explanation. So `dropped` holds the exact reason
string the gate produced -- not a re-worded copy -- and `kept` holds what the gate
actually returned.

Nothing here computes anything about a stock. It only remembers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

# A name's status at one stage.
PASSED = "passed"
DROPPED = "dropped"
NOT_REACHED = "not_reached"      # it was already out before this stage ran


@dataclass
class StageResult:
    """One gate: what went in, what came out, and why the difference."""
    key: str
    title: str
    rule: str                    # plain language, for a reader who is not a quant
    setting: str = ""            # the config key that sets it, so it can be changed
    n_in: int = 0
    kept: List[str] = field(default_factory=list)
    dropped: Dict[str, str] = field(default_factory=dict)
    note: str = ""

    @property
    def n_out(self) -> int:
        return len(self.kept)

    @property
    def n_dropped(self) -> int:
        return len(self.dropped)

    @property
    def reconciles(self) -> bool:
        """
        in - dropped == out.

        A funnel whose arithmetic does not close is lying about something, and it
        is the kind of lie nobody notices by eye. Asserted in the tests.
        """
        return self.n_in - self.n_dropped == self.n_out

    def status_of(self, ticker: str) -> str:
        if ticker in self.dropped:
            return DROPPED
        return PASSED if ticker in self.kept else NOT_REACHED


@dataclass
class DecisionTrail:
    """The stages in the order they ran."""
    stages: List[StageResult] = field(default_factory=list)

    # ---------------------------------------------------------------- writing
    def record(
        self,
        key: str,
        title: str,
        rule: str,
        *,
        kept: Sequence[str],
        dropped: Optional[Dict[str, str]] = None,
        n_in: Optional[int] = None,
        setting: str = "",
        note: str = "",
    ) -> StageResult:
        """
        Append a stage. `n_in` defaults to the previous stage's output, which is
        what makes the chain a chain rather than a list of unrelated counts.
        """
        if n_in is None:
            n_in = self.stages[-1].n_out if self.stages else len(kept) + len(dropped or {})
        stage = StageResult(
            key=key, title=title, rule=rule, setting=setting,
            n_in=int(n_in), kept=list(kept), dropped=dict(dropped or {}), note=note,
        )
        self.stages.append(stage)
        return stage

    # ---------------------------------------------------------------- reading
    def stage(self, key: str) -> Optional[StageResult]:
        return next((s for s in self.stages if s.key == key), None)

    @property
    def universe(self) -> List[str]:
        """Everything that entered the first stage, dropped or not."""
        if not self.stages:
            return []
        first = self.stages[0]
        return list(dict.fromkeys(list(first.kept) + list(first.dropped)))

    @property
    def survivors(self) -> List[str]:
        return list(self.stages[-1].kept) if self.stages else []

    def reconciles(self) -> bool:
        """Every stage closes, and each stage starts where the last one ended."""
        if not self.stages:
            return True
        if not all(s.reconciles for s in self.stages):
            return False
        return all(b.n_in == a.n_out for a, b in zip(self.stages, self.stages[1:]))

    def journey(self, ticker: str) -> List[dict]:
        """
        One row per stage for a single name -- the answer to "what happened to it".

        A name that was dropped at stage 3 reads NOT_REACHED at stages 4 and 5
        rather than DROPPED again: it did not fail those gates, it never saw them,
        and conflating the two would blame the wrong rule.
        """
        rows = []
        out_already = False
        for s in self.stages:
            status = s.status_of(ticker)
            if out_already:
                status = NOT_REACHED
            elif status == DROPPED:
                out_already = True
            rows.append({
                "key": s.key,
                "title": s.title,
                "status": status,
                "detail": s.dropped.get(ticker, "") if status == DROPPED else "",
            })
        return rows

    def outcome(self, ticker: str) -> str:
        """One line: bought, or the stage and reason it fell out."""
        for row in self.journey(ticker):
            if row["status"] == DROPPED:
                return f"{row['title']}: {row['detail']}"
        return "made it through every stage" if ticker in self.survivors else "not in the universe"

    def funnel(self) -> List[dict]:
        """Stage rows with a width fraction for the bar, relative to the start."""
        if not self.stages:
            return []
        start = max(1, self.stages[0].n_in)
        return [{
            "key": s.key, "title": s.title, "rule": s.rule, "setting": s.setting,
            "n_in": s.n_in, "n_out": s.n_out, "n_dropped": s.n_dropped,
            "width": s.n_out / start, "note": s.note,
        } for s in self.stages]

    def as_payload(self) -> dict:
        """
        Everything the searchable per-stock trace needs, as plain JSON types.

        Carries all names including the ones that fell out first -- those are
        precisely the ones somebody searches for.
        """
        return {
            "stages": [{"key": s.key, "title": s.title} for s in self.stages],
            "names": {t: {"rows": self.journey(t), "outcome": self.outcome(t)}
                      for t in self.universe},
        }
