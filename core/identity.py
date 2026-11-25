from core.i18n import t
from core.profiles import IdentityData
from core.storage import project_path, read_json


class Identity:
    def __init__(self, path="data/identity.json"):
        self.path = project_path(path)
        self.data = self.load()

    def load(self) -> IdentityData:
        return IdentityData.model_validate(read_json(self.path))

    @property
    def name(self):
        return self.data.name

    def prompt(self):
        profile = self.data
        lines = [t("identity.you_are", name=profile.name), profile.identity, profile.role]

        if profile.values:
            lines.append("\n" + t("identity.values"))
            lines.extend(f"- {value}" for value in profile.values)

        if profile.limits:
            lines.append("\n" + t("identity.limits"))
            lines.extend(f"- {limit}" for limit in profile.limits)

        communication = profile.communication
        if communication.language:
            lines.append("\n" + t("identity.language", language=communication.language))

        if communication.address_user_as:
            addresses = t("identity.address_join").join(communication.address_user_as)
            lines.append(t("identity.address", names=addresses))

        if communication.concise:
            lines.append(t("identity.concise"))

        return "\n".join(lines)
