from core.storage import StorageSyncError, atomic_write_json, project_path, read_json
from core.profiles import RelationshipData
from core.i18n import t


class Relationship:
    def __init__(self, path="data/relationship.json"):
        self.path = project_path(path)
        self.data = self.load()

    def load(self):
        return RelationshipData.model_validate(read_json(self.path)).model_dump()

    def save(self):
        atomic_write_json(self.path, RelationshipData.model_validate(self.data).model_dump())

    def prompt(self):
        lines = [t("relationship.title", percent=int(self.familiarity * 100))]
        lines.append(t("relationship.style", style=self.interaction_style))
        for key, values in (("relationship.shared", self.shared_experiences), ("relationship.topics", self.recurring_topics),
                            ("relationship.preferences", self.conversation_preferences),
                            ("relationship.moments", self.important_moments)):
            if values:
                lines.append(t(key, values=", ".join(values)))
        return "\n".join(lines)

    @property
    def familiarity(self):
        return self.data.get("familiarity", 0.0)

    @property
    def interaction_style(self):
        return self.data.get("interaction_style", "casual")

    @property
    def shared_experiences(self):
        return self.data.get("shared_experiences", [])

    @property
    def conversation_preferences(self):
        return self.data.get("conversation_preferences", [])

    @property
    def recurring_topics(self):
        return self.data.get("recurring_topics", [])

    @property
    def important_moments(self):
        return self.data.get("important_moments", [])

    def update(self, **changes):
        candidate = RelationshipData.model_validate({**self.data, **changes}).model_dump()
        try:
            atomic_write_json(self.path, candidate)
        except StorageSyncError:
            self.data = candidate
            raise
        self.data = candidate

    def set_familiarity(self, value):
        self.update(familiarity=value)

    def _append(self, key, value):
        if value not in self.data[key]:
            self.update(**{key: [*self.data[key], value]})

    def add_shared_experience(self, experience):
        self._append("shared_experiences", experience)

    def add_recurring_topic(self, topic):
        self._append("recurring_topics", topic)

    def add_important_moment(self, moment):
        self._append("important_moments", moment)
