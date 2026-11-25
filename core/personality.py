import json

from core.i18n import t
from core.profiles import PersonalityData
from core.storage import project_path, read_json


class Personality:
    def __init__(self, path="config/personality.json"):
        self.path = project_path(path)
        self.data = self.load()

    def load(self) -> PersonalityData:
        return PersonalityData.model_validate(read_json(self.path))

    def prompt(self):
        profile = self.data
        lines = [t("personality.title")]

        if profile.tone:
            lines.append(t("personality.tone", values=", ".join(profile.tone)))

        if profile.style:
            lines.append(t("personality.style", values=", ".join(profile.style)))

        if profile.humor.enabled:
            lines.append(t("personality.humor", style=profile.humor.style))

        if profile.traits:
            lines.append(t("personality.traits", traits=json.dumps(profile.traits, ensure_ascii=False)))

        if profile.behavior:
            lines.append("\n" + t("personality.behavior"))
            lines.extend(f"- {rule}" for rule in profile.behavior)

        return "\n".join(lines)
