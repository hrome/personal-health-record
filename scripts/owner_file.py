"""Helpers for reading and writing archive_owner.json.

archive_owner.json lives at the root of each medical archive and is the
canonical source of truth for who the archive belongs to. See SKILL.md
section "Archive Owner Identity".
"""

import json
import os
import re
import tempfile
from datetime import datetime, timezone

OWNER_FILENAME = "archive_owner.json"
SCHEMA_VERSION = 1


def owner_file_path(base_path: str) -> str:
    return os.path.join(base_path, OWNER_FILENAME)


def normalize_patient_name(name):
    if name is None:
        return None
    s = str(name).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s or None


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _atomic_write_json(path: str, data: dict) -> None:
    dirpath = os.path.dirname(path) or "."
    os.makedirs(dirpath, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".archive_owner.", suffix=".tmp", dir=dirpath)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_owner_file(base_path: str):
    """Return the parsed owner file dict, or None if it does not exist.

    Raises ValueError if the file exists but has an unsupported schema_version
    or invalid structure.
    """
    path = owner_file_path(base_path)
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{OWNER_FILENAME}: expected a JSON object at top level")
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"{OWNER_FILENAME}: unsupported schema_version={version!r} "
            f"(this build understands {SCHEMA_VERSION})"
        )
    if not isinstance(data.get("owner"), dict):
        raise ValueError(f"{OWNER_FILENAME}: missing 'owner' object")
    return data


def write_owner_file(base_path: str, *, patient_full_name: str,
                     patient_dob=None, aliases=None,
                     display_name=None, notes=None) -> dict:
    """Atomically (re)write the archive_owner.json file."""
    now = _now_iso()
    data = {
        "schema_version": SCHEMA_VERSION,
        "created_at": now,
        "updated_at": now,
        "owner": {
            "patient_full_name": patient_full_name,
            "patient_dob": patient_dob,
            "aliases": list(aliases or []),
        },
        "archive": {
            "display_name": display_name,
            "notes": notes,
        },
    }
    _atomic_write_json(owner_file_path(base_path), data)
    return data


def add_alias(base_path: str, new_alias: str) -> "dict | None":
    """Append `new_alias` to owner.aliases if not already present (normalized).

    Returns the updated owner dict, or None if no owner file exists.
    Raises ValueError if `new_alias` is empty.
    """
    data = read_owner_file(base_path)
    if data is None:
        return None
    norm_new = normalize_patient_name(new_alias)
    if norm_new is None:
        raise ValueError("Alias must be a non-empty string")
    owner = data["owner"]
    canonical = owner.get("patient_full_name")
    aliases = list(owner.get("aliases") or [])
    known = {normalize_patient_name(canonical)} | {
        normalize_patient_name(a) for a in aliases
    }
    if norm_new not in known:
        aliases.append(new_alias)
        owner["aliases"] = aliases
        data["updated_at"] = _now_iso()
        _atomic_write_json(owner_file_path(base_path), data)
    return data


def canonical_name_set(owner_dict: dict) -> set:
    """Normalized set of {patient_full_name} ∪ aliases used for matching."""
    owner = owner_dict.get("owner") or {}
    names = [owner.get("patient_full_name")] + list(owner.get("aliases") or [])
    return {n for n in (normalize_patient_name(x) for x in names) if n}


def canonical_name_list(owner_dict: dict) -> "list[str]":
    """Original (non-normalized) {patient_full_name} ∪ aliases, in order."""
    owner = owner_dict.get("owner") or {}
    result = []
    canonical = owner.get("patient_full_name")
    if canonical:
        result.append(canonical)
    for a in owner.get("aliases") or []:
        if a:
            result.append(a)
    return result
