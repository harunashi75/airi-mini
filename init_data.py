"""Create missing personal data from public examples without replacing existing files."""
from pathlib import Path
import shutil

from core.storage import StorageError, lock_data_directory, project_path


def initialize_data(examples, *, report=None):
    """Copy missing examples while the caller holds the data directory lock."""
    destination = project_path("data")
    for source in examples.glob("*.json"):
        target = destination / source.name
        try:
            with source.open("rb") as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
        except FileExistsError:
            if report:
                report(f"Conservé : {target.name}")
            continue
        target.chmod(0o600)
        if report:
            report(f"Créé : {target.name}")


def main():
    try:
        with lock_data_directory():
            initialize_data(Path(__file__).resolve().parent / "data.example", report=print)
    except StorageError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
