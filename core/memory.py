from threading import RLock
from core.i18n import t
from pydantic import BaseModel, ConfigDict, Field
from core.storage import StorageSyncError, atomic_write_json, project_path, read_json


class MemoryData(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    long_term: list[str] = Field(default_factory=list, max_length=200)


class MemoryManager:
    def __init__(self, path="data/memory.json"):
        self.path = project_path(path)
        self.lock = RLock()
        self.load()

    def load(self):
        self.long_term = MemoryData.model_validate(read_json(self.path, {"long_term": []})).long_term

    def _commit(self, facts):
        data = MemoryData(long_term=facts)
        try:
            atomic_write_json(self.path, data.model_dump())
        except StorageSyncError:
            self.long_term = data.long_term
            raise
        self.long_term = data.long_term

    def save(self):
        with self.lock:
            self._commit(self.long_term)

    def add_fact(self, fact: str):
        fact = fact.strip()
        if not fact or len(fact) > 1000:
            raise ValueError(t("memory.length"))
        with self.lock:
            if fact not in self.long_term:
                if len(self.long_term) >= 200:
                    raise ValueError(t("memory.full"))
                self._commit([*self.long_term, fact])

    def remove_fact(self, fact: str):
        with self.lock:
            if fact not in self.long_term:
                return False
            self._commit([item for item in self.long_term if item != fact])
            return True

    def remove_at(self, number: int):
        """Remove the fact shown as `number` by /souvenirs (1-based) and return it."""
        with self.lock:
            if not 1 <= number <= len(self.long_term):
                raise ValueError(t("forget.bad_number", count=len(self.long_term)) if self.long_term
                                 else t("memories.empty"))
            fact = self.long_term[number - 1]
            self._commit([*self.long_term[:number - 1], *self.long_term[number:]])
            return fact

    def contains(self, fact: str):
        wanted = " ".join(fact.casefold().split())
        return any(" ".join(item.casefold().split()) == wanted for item in self.long_term)

    def merge(self, facts):
        """Append unknown facts up to the 200 limit; returns (added, left out)."""
        with self.lock:
            new = [fact for fact in dict.fromkeys(facts) if fact not in self.long_term]
            room = max(0, 200 - len(self.long_term))
            if new[:room]:
                self._commit([*self.long_term, *new[:room]])
            return min(len(new), room), max(0, len(new) - room)

    def clear(self):
        with self.lock:
            self._commit([])
