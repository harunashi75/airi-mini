"""Export all personal data as one JSON bundle, and import it back safely."""
from datetime import datetime, timezone
import logging
from pydantic import ValidationError
from core.i18n import t
from core.memory import MemoryData
from core.profiles import IdentityData, MoodData, PersonalityData, RelationshipData
from core.sessions import Session
from core.storage import StorageError, atomic_write_json, read_json

logger = logging.getLogger(__name__)
FORMAT = "airi-mini-export"
VERSION = 1


def _plain_object(data):
    if not isinstance(data, dict):
        raise ValueError(t("backup.not_object"))
    return data


# Files replaced only when the user asks to restore the profile (identity, tone, relationship…).
PROFILE_FILES = {
    "data/identity.json": lambda data: IdentityData.model_validate(data).model_dump(),
    "data/mood.json": lambda data: MoodData.model_validate(data).model_dump(),
    "data/relationship.json": lambda data: RelationshipData.model_validate(data).model_dump(),
    "data/user_profile.json": _plain_object,
    "data/heritage_haru.json": _plain_object,
    "config/personality.json": lambda data: PersonalityData.model_validate(data).model_dump(),
}


def export_bundle(brain):
    sessions, unreadable = [], []
    for path in sorted(brain.sessions.directory.glob("*.json")) if brain.sessions.directory.exists() else []:
        try:
            sessions.append(brain.sessions.get(path.stem).model_dump())
        except (StorageError, ValueError):
            unreadable.append(path.name)
    profile = {}
    for name, path in brain.profile_files.items():
        try:
            profile[name] = read_json(path)
        except StorageError:
            unreadable.append(name)
    return {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "memory": list(brain.memory_manager.long_term),
        "profile": profile,
        "sessions": sessions,
        "skipped": unreadable,
    }


def _validate(bundle):
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        raise ValueError(t("backup.not_export"))
    if bundle.get("version") != VERSION:
        raise ValueError(t("backup.bad_version", version=bundle.get("version")))
    try:
        memory = MemoryData(long_term=[fact for fact in bundle.get("memory", [])]).long_term
        sessions = [Session.model_validate(item) for item in bundle.get("sessions", [])]
    except (ValidationError, TypeError) as exc:
        raise ValueError(t("backup.bad_content")) from exc
    profile = {}
    for name, data in (bundle.get("profile") or {}).items():
        if name not in PROFILE_FILES:
            raise ValueError(t("backup.unexpected_file", name=name))
        try:
            profile[name] = PROFILE_FILES[name](data)
        except (ValidationError, ValueError) as exc:
            raise ValueError(t("backup.bad_file", name=name)) from exc
    return memory, sessions, profile


def preview_bundle(brain, bundle):
    """What an import would do, without writing anything."""
    memory, sessions, profile = _validate(bundle)
    existing = {item["id"] for item in brain.sessions.list()}
    known = set(brain.memory_manager.long_term)
    return {
        "sessions_new": sum(session.id not in existing for session in sessions),
        "sessions_existing": sum(session.id in existing for session in sessions),
        "facts_new": len([fact for fact in dict.fromkeys(memory) if fact not in known]),
        "profile_files": sorted(profile),
    }


def import_bundle(brain, bundle, include_profile=False):
    """Validate everything first, then add missing conversations, merge memories, optionally restore the profile."""
    memory, sessions, profile = _validate(bundle)
    with brain.lock:
        existing = {item["id"] for item in brain.sessions.list()}
        added = 0
        for session in sessions:
            if session.id in existing:
                continue
            atomic_write_json(brain.sessions._path(session.id), session.model_dump())
            added += 1
        facts_added, facts_over_limit = brain.memory_manager.merge(memory)
        replaced = []
        if include_profile:
            paths = brain.profile_files
            for name, data in profile.items():
                atomic_write_json(paths[name], data)
                replaced.append(name)
            brain.reload_profile()
        report = {"sessions_added": added, "sessions_skipped": len(sessions) - added,
                  "facts_added": facts_added, "facts_over_limit": facts_over_limit,
                  "profile_files": replaced}
        logger.info("Import terminé : %s", report)
        return report
