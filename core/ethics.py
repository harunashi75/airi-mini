from core.i18n import t


def ethics_prompt(user, assistant="AIRI"):
    """Core principles, addressed to the user named in the profile."""
    rules = t("ethics.rules", user=user, assistant=assistant).splitlines()
    return t("ethics.title", assistant=assistant) + "\n" + "\n".join(f"- {rule}" for rule in rules)
