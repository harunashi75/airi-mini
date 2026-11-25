from core.i18n import t
from core.profiles import MoodData
from core.storage import project_path, read_json


class Mood:
    def __init__(self, path="data/mood.json"):
        self.path = project_path(path)
        self.data = self.load()

    def load(self) -> MoodData:
        return MoodData.model_validate(read_json(self.path))

    def prompt(self):
        profile = self.data
        return t("mood.prompt", mood=profile.mood, energy=profile.energy, humor=profile.humor,
                 verbosity=profile.verbosity)
