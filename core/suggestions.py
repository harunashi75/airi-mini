"""Memory suggestions: AIRI may propose a fact, only the user can save it."""
from dataclasses import dataclass
from core.i18n import t

MARKER = "[[retenir:"
END = "]]"
MAX_SUGGESTION_CHARS = 300


def suggestion_prompt(user):
    return t("prompt.suggestion", user=user, marker=MARKER, end=END)


@dataclass(frozen=True)
class MemorySuggestion:
    fact: str


class SuggestionFilter:
    """Streams visible text while capturing a trailing [[retenir: ...]] marker split across tokens."""

    def __init__(self):
        self.pending = ""
        self.captured = None  # None until the marker starts

    def feed(self, text):
        if self.captured is not None:
            self.captured += text
            return ""
        self.pending += text
        start = self.pending.find(MARKER)
        if start != -1:
            visible, self.captured = self.pending[:start], self.pending[start + len(MARKER):]
            self.pending = ""
            return visible
        # Hold back only a tail that could still become the marker.
        keep = 0
        for size in range(min(len(MARKER) - 1, len(self.pending)), 0, -1):
            if MARKER.startswith(self.pending[-size:]):
                keep = size
                break
        visible = self.pending[:len(self.pending) - keep]
        self.pending = self.pending[len(visible):]
        return visible

    def finish(self):
        """Remaining visible text and the suggested fact, if any."""
        visible, self.pending = self.pending, ""
        if self.captured is None:
            return visible, None
        fact = self.captured.split(END, 1)[0].splitlines()[0] if self.captured.strip() else ""
        fact = " ".join(fact.split())
        return visible, fact if 3 <= len(fact) <= MAX_SUGGESTION_CHARS else None
