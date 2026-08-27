"""Helpers shared by the archive scripts.

`sha1_of_file` in particular must stay a single implementation: the hash it
returns is the name the file receives in `original_files/`, and the ingestion
pipeline relies on every script agreeing on it.
"""

import hashlib
import os


def sha1_of_file(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def get_db_path(base_path: str) -> str:
    return os.path.join(base_path, "structured_database", "medical.db")
