"""Project-relative paths and crash-resistant JSON persistence."""
from contextlib import ExitStack, contextmanager
import errno
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from core.i18n import t

ROOT = Path(os.environ.get("AIRIMINI_HOME", Path(__file__).resolve().parent.parent))
logger = logging.getLogger(__name__)


class StorageError(RuntimeError):
    pass


class StorageSyncError(StorageError):
    """The replacement is visible, but saving it across a crash could not be confirmed."""


class DataDirectoryBusy(StorageError):
    pass


def project_path(path):
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def _lock_windows(fd):
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    lock = kernel32.LockFile
    unlock = kernel32.UnlockFile
    for operation in (lock, unlock):
        operation.argtypes = [wintypes.HANDLE] + [wintypes.DWORD] * 4
        operation.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(fd)
    if not lock(handle, 0, 0, 1, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return lambda: unlock(handle, 0, 0, 1, 0)


@contextmanager
def lock_data_directory():
    path = project_path("data/.airi.lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise StorageError(t("storage.lock_open", path=path.parent)) from exc
    with ExitStack() as cleanup:
        # Leave the file in place so every instance locks the same file.
        cleanup.callback(os.close, fd)
        try:
            if os.name == "nt":
                cleanup.callback(_lock_windows(fd))
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN) or getattr(exc, "winerror", None) == 33:  # ERROR_LOCK_VIOLATION
                raise DataDirectoryBusy(t("storage.busy", path=path.parent)) from exc
            raise StorageError(t("storage.lock_failed", path=path.parent)) from exc
        yield


def read_json(path, default=None):
    path = project_path(path)
    if not path.exists() and default is not None:
        return default
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        logger.error("Lecture JSON impossible : %s", path)
        raise StorageError(t("storage.unreadable", name=path.name)) from exc


def atomic_write_json(path, data):
    """Never replace unreadable data; retain the previous valid version."""
    path = project_path(path)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            read_json(path)
        encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            backup = path.with_suffix(path.suffix + ".bak")
            # Copy through a temporary file so the previous backup also survives interruption.
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as backup_handle:
                backup_temp = Path(backup_handle.name)
            try:
                shutil.copyfile(path, backup_temp)
                os.replace(backup_temp, backup)
            finally:
                backup_temp.unlink(missing_ok=True)
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        raise StorageError(t("storage.save_failed", name=path.name)) from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                logger.warning("Fichier temporaire non supprimé : %s", temporary, exc_info=True)

    # Windows does not allow opening directories with os.open().
    if os.name != "nt":
        try:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            raise StorageSyncError(t("storage.sync_failed", name=path.name)) from exc
