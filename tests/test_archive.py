#!/usr/bin/env python3
"""Regression tests for the archive scripts.

Run with:  python3 -m unittest discover -s tests
Standard library only — the skill has no third-party dependencies.
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import check_db_duplicates
import owner_file
import query_db
import save_extraction
from delete_from_archive import delete_from_archive, get_file_summary


def extraction(patient="John Doe", date="2024-01-01", lab="LabX",
               indicators=("Hemoglobin",), policy=None):
    return {
        "document_type": "lab_result",
        "language": "en",
        "brief_description": "test lab",
        "raw_text": "x",
        "metadata": {
            "patient_full_name": patient,
            "patient_dob": "1990-01-15",
            "policy_number": policy,
            "clinic_or_lab_name": lab,
            "doctor_full_name": None,
            "document_date": date,
        },
        "structured": {
            "collection_date": date,
            "laboratory_name": lab,
            "indicators": [{"indicator_name": n, "value_raw": "1"} for n in indicators],
        },
    }


class ArchiveTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="phr-test-")
        self.base = os.path.join(self.tmp, "archive")
        os.makedirs(self.base)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def make_file(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def ingest(self, path, ext=None, **kwargs):
        return save_extraction.save_to_archive(
            path, self.base, ext or extraction(), **kwargs
        )

    def count_rows(self, table):
        with sqlite3.connect(os.path.join(self.base, "structured_database", "medical.db")) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TestForceReingest(ArchiveTestCase):
    """--force must replace the previous rows, not add a second set."""

    def test_force_does_not_duplicate_typed_rows(self):
        path = self.make_file("lab.pdf", "content")
        self.ingest(path)
        for _ in range(2):
            result = self.ingest(path, force=True)

        self.assertEqual(result["status"], "ingested")
        self.assertTrue(result["reprocessed"])
        self.assertEqual(self.count_rows("files"), 1)
        self.assertEqual(self.count_rows("lab_events"), 1)
        self.assertEqual(self.count_rows("lab_indicators"), 1)

    def test_force_picks_up_the_new_extraction(self):
        path = self.make_file("lab.pdf", "content")
        self.ingest(path, ext=extraction(indicators=("Hemoglobin",)))
        self.ingest(path, ext=extraction(indicators=("Hemoglobin", "Ferritin")), force=True)
        self.assertEqual(self.count_rows("lab_indicators"), 2)

    def test_plain_reingest_is_reported_as_duplicate(self):
        path = self.make_file("lab.pdf", "content")
        self.ingest(path)
        self.assertEqual(self.ingest(path)["status"], "duplicate")


class TestIngestIsAtomic(ArchiveTestCase):
    """A failed ingest must leave nothing behind for the dedup check to trip on."""

    def test_failure_rolls_back_copied_files(self):
        path = self.make_file("lab.pdf", "content")
        json_dir = os.path.join(self.base, "json_extractions")
        os.makedirs(json_dir)
        os.chmod(json_dir, 0o555)
        self.addCleanup(os.chmod, json_dir, 0o755)

        with self.assertRaises(OSError):
            self.ingest(path)

        orig_dir = os.path.join(self.base, "original_files")
        self.assertEqual(os.listdir(orig_dir) if os.path.isdir(orig_dir) else [], [])
        self.assertEqual(os.listdir(json_dir), [])

    def test_retry_after_failure_succeeds(self):
        path = self.make_file("lab.pdf", "content")
        json_dir = os.path.join(self.base, "json_extractions")
        os.makedirs(json_dir)
        os.chmod(json_dir, 0o555)
        with self.assertRaises(OSError):
            self.ingest(path)
        os.chmod(json_dir, 0o755)

        self.assertEqual(self.ingest(path)["status"], "ingested")
        self.assertEqual(self.count_rows("files"), 1)


class TestDelete(ArchiveTestCase):
    def test_delete_works_when_original_is_missing(self):
        path = self.make_file("lab.pdf", "content")
        sha1 = self.ingest(path)["sha1"]
        os.remove(os.path.join(self.base, "original_files", f"{sha1}.pdf"))

        result = delete_from_archive(self.base, sha1)

        self.assertEqual(result["status"], "deleted")
        self.assertFalse(result["deleted_original_file"])
        self.assertTrue(result["deleted_json_extraction"])
        self.assertEqual(self.count_rows("files"), 0)
        self.assertEqual(self.count_rows("lab_indicators"), 0)

    def test_delete_removes_every_layer(self):
        path = self.make_file("lab.pdf", "content")
        sha1 = self.ingest(path)["sha1"]

        delete_from_archive(self.base, sha1)

        self.assertIsNone(get_file_summary(self.base, sha1))
        self.assertFalse(os.path.exists(os.path.join(self.base, "original_files", f"{sha1}.pdf")))
        self.assertFalse(os.path.exists(os.path.join(self.base, "json_extractions", f"{sha1}.json")))

    def test_unknown_sha1_is_not_found(self):
        path = self.make_file("lab.pdf", "content")
        self.ingest(path)
        self.assertEqual(delete_from_archive(self.base, "0" * 40)["status"], "not_found")


class TestDbDuplicateCheck(ArchiveTestCase):
    def test_no_database_yet_is_not_an_error(self):
        path = self.make_file("lab.pdf", "content")
        result = check_db_duplicates.check(self.base, path, extraction())
        self.assertEqual(result["status"], "no_match")
        self.assertEqual(result["reason"], "db_not_initialized")

    def test_same_bytes_are_an_exact_duplicate(self):
        path = self.make_file("lab.pdf", "content")
        self.ingest(path)
        result = check_db_duplicates.check(self.base, path, extraction())
        self.assertEqual(result["status"], "exact_duplicate")

    def test_rescan_of_the_same_document_is_a_content_duplicate(self):
        self.ingest(self.make_file("scan1.pdf", "first scan"))
        rescan = self.make_file("scan2.pdf", "second scan of the same paper")
        result = check_db_duplicates.check(self.base, rescan, extraction())
        self.assertEqual(result["status"], "content_duplicate")

    def test_different_document_same_day_is_only_a_possible_duplicate(self):
        self.ingest(self.make_file("morning.pdf", "a"))
        other = self.make_file("evening.pdf", "b")
        result = check_db_duplicates.check(
            self.base, other, extraction(lab="OtherLab", indicators=("Ferritin",))
        )
        self.assertEqual(result["status"], "possible_duplicate")

    def test_missing_document_date_skips_the_check(self):
        self.ingest(self.make_file("lab.pdf", "a"))
        other = self.make_file("undated.pdf", "b")
        result = check_db_duplicates.check(self.base, other, extraction(date=None))
        self.assertEqual(result["status"], "skipped")


class TestOwnerIdentity(ArchiveTestCase):
    def test_first_ingest_creates_the_owner_file(self):
        result = self.ingest(self.make_file("lab.pdf", "a"))
        self.assertTrue(result["owner_file_created"])
        owner = owner_file.read_owner_file(self.base)
        self.assertEqual(owner["owner"]["patient_full_name"], "John Doe")

    def test_name_matching_ignores_case_and_extra_whitespace(self):
        self.ingest(self.make_file("lab.pdf", "a"))
        result = self.ingest(self.make_file("lab2.pdf", "b"),
                             ext=extraction(patient="  john   DOE ", date="2024-02-02"))
        self.assertEqual(result["status"], "ingested")

    def test_another_patient_is_rejected(self):
        self.ingest(self.make_file("lab.pdf", "a"))
        result = self.ingest(self.make_file("lab2.pdf", "b"),
                             ext=extraction(patient="Jane Roe", date="2024-02-02"))
        self.assertEqual(result["status"], "patient_mismatch")
        self.assertTrue(result["owner_file_present"])
        self.assertEqual(self.count_rows("files"), 1)

    def test_add_alias_lets_the_same_person_through(self):
        self.ingest(self.make_file("lab.pdf", "a"))
        result = self.ingest(self.make_file("lab2.pdf", "b"),
                             ext=extraction(patient="Doe J.", date="2024-02-02"),
                             add_alias_first="Doe J.")
        self.assertEqual(result["status"], "ingested")
        self.assertIn("Doe J.", owner_file.read_owner_file(self.base)["owner"]["aliases"])

    def test_alias_without_an_owner_file_is_refused(self):
        result = self.ingest(self.make_file("lab.pdf", "a"), add_alias_first="Someone")
        self.assertEqual(result["status"], "no_owner_file")

    def test_policy_number_verifies_a_file_with_no_name(self):
        self.ingest(self.make_file("lab.pdf", "a"), ext=extraction(policy="1234 5678 9012 3456"))
        result = self.ingest(self.make_file("lab2.pdf", "b"),
                             ext=extraction(patient=None, date="2024-02-02",
                                            policy="1234567890123456"))
        self.assertEqual(result["status"], "ingested")

    def test_wrong_policy_number_is_a_mismatch(self):
        self.ingest(self.make_file("lab.pdf", "a"), ext=extraction(policy="1111111111111111"))
        result = self.ingest(self.make_file("lab2.pdf", "b"),
                             ext=extraction(patient=None, date="2024-02-02",
                                            policy="2222222222222222"))
        self.assertEqual(result["status"], "patient_mismatch")
        self.assertEqual(result["match_basis"], "policy_number")

    def test_unnamed_file_passes_when_no_policy_is_on_record(self):
        self.ingest(self.make_file("lab.pdf", "a"))
        result = self.ingest(self.make_file("lab2.pdf", "b"),
                             ext=extraction(patient=None, date="2024-02-02"))
        self.assertEqual(result["status"], "ingested")

    def test_unsupported_schema_version_is_rejected(self):
        owner_file.write_owner_file(self.base, patient_full_name="John Doe")
        path = owner_file.owner_file_path(self.base)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data["schema_version"] = 99
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        with self.assertRaises(ValueError):
            owner_file.read_owner_file(self.base)


class TestQueryIsReadOnly(ArchiveTestCase):
    def test_select_works(self):
        self.ingest(self.make_file("lab.pdf", "a"))
        db = os.path.join(self.base, "structured_database", "medical.db")
        rows = query_db.run_query(db, "SELECT original_filename FROM files")
        self.assertEqual(rows, [{"original_filename": "lab.pdf"}])

    def test_writes_are_rejected(self):
        self.ingest(self.make_file("lab.pdf", "a"))
        db = os.path.join(self.base, "structured_database", "medical.db")
        with self.assertRaises(sqlite3.OperationalError):
            query_db.run_query(db, "DELETE FROM files")
        self.assertEqual(self.count_rows("files"), 1)


if __name__ == "__main__":
    unittest.main()
