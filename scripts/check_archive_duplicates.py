#!/usr/bin/env python3
"""
Check the archive (filesystem) for already-ingested files.

Step 1 of the ingestion pipeline. For each input file path this computes the
SHA-1 and looks for `{sha1}.*` in `original_files/`. A file is considered
already archived if such a match exists on disk.

The SHA-1 is computed with the same chunked logic as save_extraction.py, so the
hash printed here is exactly the name the file would receive in original_files/.

One JSON object is printed per input file (one per line):

    {"original_filename": "cbc.pdf", "path": "/tmp/cbc.pdf",
     "sha1": "abc123...", "status": "absent"}
    {"original_filename": "mri.pdf", "path": "/tmp/mri.pdf",
     "sha1": "def456...", "status": "present", "archived_file": "def456....pdf"}
    {"original_filename": "gone.pdf", "path": "/tmp/gone.pdf",
     "status": "error", "reason": "file_not_found"}

Usage:

    python scripts/check_archive_duplicates.py \\
      --base-path ~/medical-archive \\
      file1.pdf [file2.jpg ...]
"""

import argparse
import glob
import json
import os

from common import sha1_of_file


def check_file(base_path: str, path: str) -> dict:
    record = {"original_filename": os.path.basename(path), "path": path}

    if not os.path.exists(path):
        return {**record, "status": "error", "reason": "file_not_found"}
    if os.path.isdir(path):
        return {**record, "status": "error", "reason": "is_a_directory"}

    try:
        sha1 = sha1_of_file(path)
    except OSError:
        return {**record, "status": "error", "reason": "unreadable"}

    record["sha1"] = sha1
    matches = sorted(glob.glob(
        os.path.join(base_path, "original_files", f"{sha1}.*")
    ))
    if matches:
        return {**record, "status": "present",
                "archived_file": os.path.basename(matches[0])}
    return {**record, "status": "absent"}


def main():
    parser = argparse.ArgumentParser(
        description="Filesystem deduplication check for the medical archive."
    )
    parser.add_argument("--base-path", required=True, help="Root archive directory")
    parser.add_argument("files", nargs="+", help="One or more file paths to check")
    args = parser.parse_args()

    for path in args.files:
        print(json.dumps(check_file(args.base_path, path), ensure_ascii=False))


if __name__ == "__main__":
    main()
