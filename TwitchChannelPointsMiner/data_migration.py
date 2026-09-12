"""Version and migrate user-owned analytics data files."""

import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

ANALYTICS_DATA_VERSION = 1

# Rewritten wholesale on every update (a watch cycle for now_watching.json,
# any dashboard preference change for dashboard_prefs.json), never durable
# per-streamer analytics data, so neither carries a "version" field and both
# are exempt from the versioned-JSON-object migration below. Without this,
# a file that's read-modified-written wholesale elsewhere loses whatever
# "version" a migration attempt added the moment anything next changes it -
# making every later launch see it as unmigrated again, hit the same
# already-created backup file, and fail hard.
UNVERSIONED_ANALYTICS_FILES = {"now_watching.json", "dashboard_prefs.json"}


class DataMigrationError(ValueError):
    pass


def _migrate_json_file(path, current_version):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DataMigrationError(
            f"Unable to read analytics data {path}: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise DataMigrationError(f"Analytics data {path} must contain a JSON object")

    version = payload.get("version", 0)
    if not isinstance(version, int):
        raise DataMigrationError(f"Analytics data version in {path} must be an integer")
    if version > current_version:
        raise DataMigrationError(
            f"Analytics data {path} uses unsupported version {version}; "
            f"this release supports up to {current_version}"
        )
    if version == current_version:
        return False

    backup = path.with_name(f"{path.name}.v{version}.bak")
    if backup.exists():
        raise DataMigrationError(f"Refusing to overwrite existing {backup}")

    payload["version"] = current_version
    shutil.copy2(path, backup)
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor = None
    temporary = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".migrating", dir=path.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump(payload, handle, indent=4, ensure_ascii=False)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        backup.unlink(missing_ok=True)
        raise
    return True


def migrate_analytics_directory(directory):
    path = Path(directory)
    if path.is_symlink():
        raise DataMigrationError(
            f"Refusing to migrate symlinked analytics directory {path}"
        )
    if not path.is_dir():
        return 0
    candidates = [
        file_path
        for file_path in sorted(path.glob("*.json"))
        if not file_path.is_symlink()
        and file_path.is_file()
        and file_path.name not in UNVERSIONED_ANALYTICS_FILES
    ]
    return sum(
        _migrate_json_file(file_path, ANALYTICS_DATA_VERSION)
        for file_path in candidates
    )
