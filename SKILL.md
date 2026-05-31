---
name: personal-health-record
description: >
  Medical records assistant for organizing and querying personal health documents
  (lab results, doctor visits, MRI/ultrasound/X-ray/CT/ECG, discharge summaries,
  prescriptions, vaccinations). Use when users mention adding medical files,
  querying health history, tracking lab indicator trends, reviewing past diagnoses,
  searching prescriptions, or organizing any medical paperwork. Trigger on casual
  mentions like "add my MRI results", "when was my last CBC", "show my vaccination
  history", "find all prescriptions from last year", "how has my hemoglobin changed",
  "что показал анализ крови", "добавь результаты анализов". Supports Russian and
  English documents (mixed-language documents are handled correctly).
  Also trigger reactively: after reading and analyzing any file in chat that turns
  out to be a medical document (lab results, doctor visit notes, imaging studies,
  discharge summaries, prescriptions, vaccinations) — append an offer to save it
  to the Personal Health Record archive at the end of the response, even if the
  user did not ask about the archive.
compatibility:
  - Storage: SQLite local — no external services required
---

# Personal Health Record

Reads medical documents, extracts relevant information, and keeps everything in a local 3-layer archive.
Analyzes the data on request — tracks lab trends, surfaces past diagnoses, and answers questions about health history.

## Running Scripts

> **All scripts referenced in this document live in the `scripts/` directory of
> this skill — i.e. the `scripts/` folder next to this `SKILL.md` file.** Always
> invoke them from that skill directory, never relative to the current working
> directory.
>
> When working with an archive, the current working directory is usually the
> archive folder (`BASE_PATH`), not the skill folder. A bare `python
> scripts/<name>.py` will therefore fail with "No such file or directory".
> Instead, resolve the script's absolute path inside the skill directory before
> running it — e.g. `python /path/to/skill/scripts/check_db_duplicates.py ...`,
> where `/path/to/skill` is the directory containing this `SKILL.md`.

## Reading PDFs

> **Do not install `poppler`. Do not use `pypdf` (or any other PDF library).
> Read PDFs directly through the `Read` tool.**

This skill has **no PDF-related system dependencies**. PDF reading goes
through Claude Code's native `Read` tool, which (when called without local
preprocessing) wraps the file in an Anthropic API `document` block and ships
it to the API — the model unpacks both text and rendered page images on the
server side. Nothing is rendered or parsed on the user's machine.

**Rules — apply on every PDF, every session:**

- **Call `Read(file_path)` directly on the `.pdf`. Do not use the `pages:`
  parameter.** Specifying `pages:` triggers local page extraction and
  requires `pdftoppm` / `poppler` — that is the wrong path for this skill.
- **Do not pre-render PDFs to PNG/JPG with `pdftoppm`, `poppler`,
  ImageMagick, or anything else.** Pass the original `.pdf` straight to
  `Read`.
- **Do not parse PDFs with `pdfplumber`, `pypdf`, `pdfminer`, or any other
  text-only library** — they mangle medical tables and miss scanned content.
- **Do not run diagnostic checks like `which pdftoppm`, `brew list poppler`,
  `apt list ...`** — they imply the wrong dependency story to the user.
- **Do not suggest `brew install poppler`** in error messages.

API limits per PDF: ~32 MB and ~100 pages. If a file exceeds either limit
(or `Read` fails for any other reason — password-protected, corrupted,
unsupported encoding), surface the error verbatim and ask the user to:
- compress the PDF (e.g. macOS Preview → Export → Reduce File Size),
- split it into smaller PDFs (e.g. Preview → drag pages into a new doc), or
- re-export an unlocked / clean copy.

Then re-ingest the resulting files. The skill does not provide local
splitting/compression — that is the user's responsibility.

Images (`.jpg`, `.png`, `.heic`, …) are handled by `Read` the same way: pass
the file path directly, no preprocessing.

## Architecture

```
<BASE_PATH>/
├── archive_owner.json       # Identity file — who this archive belongs to
├── original_files/          # Original files, named by SHA-1 hash (never modified)
├── json_extractions/        # Full structured JSON extraction per file
└── structured_database/
    ├── medical.db           # SQLite — 8 typed tables
    └── errors.log           # Ingestion errors
```

Query order: **structured_database first** (fastest) → **json_extractions** if
SQL is insufficient → **original_files** only when the user explicitly asks.

### `archive_owner.json`

The canonical identity file for the archive. Auto-created on the first ingest
with a non-null `patient_full_name` — **the user is never asked to type their
name**. Format:

```json
{
  "schema_version": 1,
  "created_at": "2026-05-24T10:30:00Z",
  "updated_at": "2026-05-24T10:30:00Z",
  "owner": {
    "patient_full_name": "John Doe",
    "patient_dob": "1990-01-15",
    "aliases": ["Jonathan Doe", "Doe J."]
  },
  "archive": {
    "display_name": null,
    "notes": null
  }
}
```

Safe for the user to hand-edit: `owner.aliases`, `archive.display_name`,
`archive.notes`, and `owner.patient_dob`. Do not touch `schema_version` or
`created_at` by hand.

---

## Choosing Which Archive To Use

The same machine may host **multiple archives — one per person** (yours, a parent's,
a child's, …). Before any read or write, resolve which archive is active using this
strict priority order:

**Priority 1 — current working directory.**
If the current working directory is itself the root of a medical archive, use it as
`BASE_PATH`. A directory qualifies as an archive root when **either**:
- it contains `archive_owner.json` (strongest signal), **or**
- it contains all three of `original_files/`, `json_extractions/`, and
  `structured_database/medical.db` (legacy archives without an owner file).

Detect this before falling back to anything else.

> **Sequential resolution — never parallelize.** Check Priority 1 first and
> wait for its result. Only if the current directory is **not** an archive root
> should you proceed to Priority 2. Do **not** issue a Priority-2 command
> (reading `.env`) at the same time as the Priority-1 check (`ls` / directory
> listing) — the priorities are strict and conditional, not parallel hints.

**Priority 2 — `.env` `BASE_PATH`.**
If the current working directory is **not** an archive root, read `BASE_PATH` from the
skill's `.env` file (or the inherited environment) and use that.

**If neither is available**, run First-Run Onboarding to set one up.

When you start working in a session, state which archive you resolved, how, and
who it belongs to — read `archive_owner.json` and include the owner's name:

> "Using archive at `/Users/.../medical-archive` (resolved from current working
> directory) — owner **John Doe** (DOB 1990-01-15)."

If the active archive has no `archive_owner.json` yet (a brand-new archive or a
legacy one), say so explicitly and explain that the owner will be established
from the first file that contains a patient name. This prevents accidentally
writing one person's record into another person's archive.

---

## Archive Owner Identity

**Each archive belongs to exactly one person.** A single archive must never mix
records from different patients.

The archive's owner is defined by `archive_owner.json` at the archive root
(see Architecture). `save_extraction.py` resolves the active owner in this
order:

1. **`archive_owner.json` exists** → it is authoritative. The new file's
   `metadata.patient_full_name` must match `owner.patient_full_name` or any
   entry in `owner.aliases` (whitespace-normalized, case-insensitive).
2. **File missing, DB has patient names** → legacy archive. Falls back to the
   distinct `patient_full_name`s already stored in the typed tables.
3. **File missing, DB empty** → no known owner; the next ingest with a
   non-null patient name will establish it.

### Auto-creation — never ask the user to type their name

On the first ingest with a non-null `metadata.patient_full_name` into an
archive that has no `archive_owner.json`, the script writes the owner file
automatically using that name (and `patient_dob` if available). The result
JSON will include `"owner_file_created": true` and an `"owner"` object.

When you see `owner_file_created: true`, announce it to the user:

> "Owner identified as **{patient_full_name}**{ (DOB {patient_dob}) if present}
> — saved to `archive_owner.json`. You can hand-edit `aliases`, `display_name`,
> or `notes` in that file anytime."

If the first file's `patient_full_name` is null/empty, the file is saved but
no owner file is created yet. Surface this to the user and tell them the next
file with a patient name will establish the owner.

### Validation before saving

Before calling `save_extraction.py` for a new file, you must verify that the
extracted `metadata.patient_full_name` is consistent with the active archive's
owner. `save_extraction.py` performs the same check as a safety net and will
refuse to ingest a file from a different patient — it returns
`{"status": "patient_mismatch", "archive_patient_names": [...],
"file_patient_name": "...", "owner_file_present": true|false}` and does **not**
write anything to the archive.

Matching is whitespace-normalized and case-insensitive, and the canonical name
plus all `aliases` count as the same person. Null patient names in the new
extraction do **not** trigger a hard mismatch at the script level (many
documents simply do not print the patient name) — but **you must still confirm
with the user before saving**.

When the file's `metadata.patient_full_name` is null/empty and the archive already
has a known owner, ask:

> "I couldn't identify the patient from `{filename}`. The active archive at
> `{BASE_PATH}` belongs to **{owner.patient_full_name}**. Should I save this
> file as belonging to that person? (yes / no — different person / skip this file)"

Only proceed with the save after the user confirms it belongs to the archive's
owner. If they say it's a different person, follow the mismatch flow below
(switch archive or skip). Do not silently attribute an unidentified document to
the archive's owner.

### When a mismatch occurs

If validation (either yours or the script's) reports a mismatch, **stop the ingest
for that file** and show the user:

> "This file appears to belong to **{file_patient_name}**, but the active archive
> at `{BASE_PATH}` belongs to **{archive_patient_names}**.
> I won't save it here. Options:
> A) Switch to a different archive for **{file_patient_name}** — give me its path,
>    or `cd` into its folder and try again.
> B) Skip this file.
> C) If this really is the same person (e.g. a name spelled differently),
>    confirm and I'll register **{file_patient_name}** as an alias for
>    **{archive_patient_names[0]}** in `archive_owner.json` and retry the save."

For option C, when `owner_file_present: true`, re-invoke `save_extraction.py`
with `--add-alias "{file_patient_name}"`. The script appends the alias to
`owner.aliases`, bumps `updated_at`, then re-runs the identity check and
proceeds to save in the same call.

For option C when `owner_file_present: false` (legacy archive with no owner
file yet), explain that the owner file will be created from the next ingested
file with a known name; until then, use `--allow-patient-mismatch` only after
explicit user confirmation that the file belongs to the same person.

Never bypass the check silently.

### Migrating a legacy archive (no `archive_owner.json`)

When you encounter an archive whose DB has records but no `archive_owner.json`,
ask once per session:

> "All existing records in `{BASE_PATH}` are under **{derived_name}**. Save this
> as the archive owner (`archive_owner.json`)?"

On confirmation, write the file by either ingesting a file with that
`patient_full_name` (auto-creation kicks in) or by editing `archive_owner.json`
directly to seed the canonical owner. On rejection, continue with DB-inference
fallback and do not nag again this session.

---

## First-Run Onboarding

On first use, greet the user with:

> "Hi! This skill helps you organize and analyze your medical documents.
>
> **original_files** — your original files (PDF, JPG, PNG, …), stored unchanged (named by SHA-1 hash).
> **json_extractions** — full structured JSON extracted from each file.
> **structured_database** — a local SQLite database with typed tables for each
>   document type (lab results, doctor visits, imaging studies, etc.).
>
> When you ask a question, I always query structured_database first (fastest).
> If that's not enough, I look at json_extractions. I only open original_files
> if you explicitly ask me to.
>
> Let's start — where should I store your medical archive?
> Please give me a folder path, e.g. `/Users/you/medical-archive`."

After the user provides BASE_PATH, ask:

> "Got it. How would you like to provide files?
> A) Attach files directly in this chat (great for 1–20 files)
> B) Give me a folder path on your computer — I'll read each file in this session
> C) Other source (Google Drive, email, clinic website via MCP)"

Record BASE_PATH and the chosen method for the session.

**Do not ask the user for their name, patronymic, or date of birth.** The
archive's identity file (`archive_owner.json`) is created automatically from
the first ingested file that contains a patient name. Tell the user this when
they ask whose archive it is before any file is ingested.

---

## Operational Modes

| User says | Action | Script |
|-----------|--------|--------|
| File attached in chat | in-session ingest | Claude reads + `save_extraction.py` |
| "add folder", "import all files from …" | in-session ingest (multiple files) | Claude reads each + `save_extraction.py` |
| File loaded from cloud/MCP | in-session ingest | Claude reads + `save_extraction.py` |
| Any medical question | query | `query_db.py` |
| "how many files", "what's in my archive", "show summary" | status | `query_db.py` |
| "show my timeline", "history", "what happened in 2024" | timeline | `query_db.py` |
| "how has my [indicator] changed", "trend", "chart" | trends | `query_db.py` |
| "export", "give me a CSV", "backup" | export | `export_csv.py` |
| "re-extract", "update this file", "reprocess" | reprocess | `save_extraction.py --force` |
| "delete this file", "remove from archive", "удали файл из архива" | delete | `delete_from_archive.py` |

---

## Ingestion Pipeline

Documents can reach the agent in multiple ways — attached in chat, read from a local
path on disk, or loaded from a cloud service or MCP server. The ingestion pipeline is
the same regardless of source.

**Step 1 — Hash all files first for Deduplication**
Before reading or extracting anything, compute the SHA-1 for every file in the batch
and check whether each hash already exists in the `original_files` folder
(search for `{sha1}.*` — the extension varies by file type).
This check is cheap and must always happen first.

**Step 2 — Tell the user which files are already archived and which are new**
Before any extraction starts, report the deduplication result for every file.
This per-file status output is mandatory for every upload batch, regardless of how many
files were provided and regardless of whether files are duplicates, newly saved, failed, or mixed.
For each file, show at minimum:
- file name
- SHA-1
- whether it is already present in the archive
- if already present: `import_timestamp`
- if not present: that it will now be processed and saved

If a matching file exists, do not extract it again unless the user explicitly asks to
re-process it with `--force`.

**Step 3 — Read only the files that are not yet archived**
Use the native `Read` tool on each file path that is not already present in
the archive. Skip files that already exist. For PDFs, call `Read(file_path)`
directly — **do not use the `pages:` parameter**, do not pre-render pages
locally, do not parse with text-only libraries (see **Reading PDFs**). If
`Read` fails on a PDF, surface the error to the user and ask them to
compress / split / unlock the file externally; do not propose installing
poppler.

**Step 4 — Extract structured data (Claude does this inline)**
Apply the extraction schema below and produce a JSON object with the required structure
for each new file.

**Step 5 — Verify patient identity, then save newly extracted files immediately**
Before saving each file, confirm that its extracted `metadata.patient_full_name`
matches the archive's owner (see **Archive Owner Identity**). If `save_extraction.py`
returns `status: "patient_mismatch"`, surface the mismatch prompt to the user
(option A switch / B skip / C add alias). Do not retry with
`--allow-patient-mismatch` or `--add-alias` without explicit user confirmation in
this conversation.

After the identity check passes, save each newly processed file to the archive in
the same run. Do not pause for a separate confirmation step once the user has asked
to process the files.

If the script's result includes `"owner_file_created": true`, surface the
owner-identified announcement to the user (see **Auto-creation** under Archive
Owner Identity) — this happens only on the first qualifying ingest per archive.

Use:
```bash
python scripts/save_extraction.py \
  --base-path <BASE_PATH> \
  --file-path <path_to_original_file> \
  --extraction - <<'EOF'
{"document_type": "lab_result", ...}
EOF
```

**Step 6 — Show a user-facing detailed result**
After deduplication, extraction, and saving, show a detailed result covering every file,
including files that were skipped as duplicates and files that were newly saved.
This final report is mandatory for every file in the batch, regardless of status and
regardless of the total number of files.
For each file, the output must include:
- the original file name
- the SHA-1
- whether it was skipped as already archived or newly saved
- if skipped as duplicate: still include the file in the report with its existing
  import_timestamp — never silently omit duplicate files from the final output
- the detected document type
- the document date, if available
- the clinic, lab, or provider name, if available
- the patient name, if available
- the brief description
- if newly saved: the key extracted contents from `structured`


For `lab_result`, the summary must include:
- the indicator count
- a table of extracted indicators
- the table must include, when available: indicator name, result, unit, reference range, and status
- the table must include all extracted indicators from the file, not just a subset or first few rows
- any abnormal or out-of-range indicators called out explicitly

For other document types, the summary should list the main extracted fields relevant to that type
(for example diagnosis, conclusion, medications, recommendations, visit date, study type).

---

### Extraction Schema

Claude must produce a JSON object with this top-level structure:

```json
{
  "document_type": "lab_result | doctor_visit | imaging_study | discharge_summary | prescription | vaccination | unknown",
  "language": "ru | en | mixed",
  "brief_description": "1-2 sentence summary: document type, date, provider/lab name",
  "raw_text": "full text of the document as-is",
  "metadata": {
    "patient_full_name": null,
    "patient_dob": null,
    "clinic_or_lab_name": null,
    "doctor_full_name": null,
    "document_date": null
  },
  "structured": { }
}
```

`structured` contents depend on `document_type` — see **SQLite Schema Reference** for field names.
For `lab_result`, `structured` must also contain an `indicators` array (one object per test result).

**Rules:**
- Use `null` for any field not found — never invent values.
- Preserve original field values in their source language (ru/en).
- For `range_status` in lab indicators: `normal`, `above`, `below`, or `null` if unknown.

**If no readable text or content could be extracted:**
> "Couldn't extract content from `{filename}`. It may be corrupted, password-protected,
> or a very low-resolution scan. Please try re-exporting or sending a clearer version."

**If document type is `unknown` after extraction:**
> "I extracted text from `{filename}` but couldn't identify the document type.
> Could you tell me? Options: lab result, doctor visit note, imaging study,
> discharge summary, prescription, vaccination record, or something else."

---

## Querying

Use `query_db.py` for all structured data questions.

```bash
python scripts/query_db.py --base-path <BASE_PATH> --sql "<SQL>" [--format table]
```

### Common query patterns

**Status / archive overview:**
```sql
SELECT document_type, COUNT(*) AS count FROM files GROUP BY document_type
```

**All files, newest first:**
```sql
SELECT original_filename, document_type, brief_description, import_timestamp
FROM files ORDER BY import_timestamp DESC
```

**Lab result trend (e.g. hemoglobin):**
```sql
SELECT li.collection_date, li.value_raw, li.value_numeric, li.unit,
       li.ref_range_low, li.ref_range_high, li.range_status
FROM lab_indicators li
WHERE lower(li.indicator_name_en) LIKE '%hemoglobin%'
   OR lower(li.indicator_name) LIKE '%гемоглобин%'
ORDER BY li.collection_date
```

**Out-of-range indicators:**
```sql
SELECT li.collection_date, li.indicator_name, li.value_raw, li.unit,
       li.ref_range_text, li.range_status, f.original_filename
FROM lab_indicators li
JOIN files f ON li.file_sha1 = f.sha1
WHERE li.range_status IN ('above', 'below')
ORDER BY li.collection_date DESC
```

**All diagnoses from doctor visits:**
```sql
SELECT visit_date, doctor_specialty, diagnosis_main, diagnosis_icd
FROM doctor_visits
ORDER BY visit_date DESC
```

**Timeline (all event types, given year):**
```sql
SELECT '🔬 Lab' AS type, collection_date AS date, notes AS summary, file_sha1
FROM lab_events WHERE collection_date LIKE '2024%'
UNION ALL
SELECT '🏥 Visit', visit_date, diagnosis_main || ' — ' || doctor_specialty, file_sha1
FROM doctor_visits WHERE visit_date LIKE '2024%'
UNION ALL
SELECT '📷 Imaging', study_date, study_type || ' — ' || body_region, file_sha1
FROM imaging_studies WHERE study_date LIKE '2024%'
ORDER BY date
```

Always **cite the source file** (original_filename + date) for every fact stated.

---

## Deleting Files From The Archive

Deletion is supported only by **SHA-1 hash**. Do not accept filename-only deletion requests.

### Required flow

1. Ask the user for the file SHA-1 if they have not provided it yet.
2. Run:

```bash
python scripts/delete_from_archive.py --base-path <BASE_PATH> --sha1 <SHA1>
```

3. Show the user a short summary from the returned JSON before deleting anything.
   The summary must include at minimum:
   - `sha1`
   - `original_filename`
   - `import_timestamp`
   - `document_type`
   - `brief_description`
   - whether the original file and JSON extraction currently exist
4. Ask for explicit confirmation.
5. Only after the user confirms, run:

```bash
python scripts/delete_from_archive.py \
  --base-path <BASE_PATH> \
  --sha1 <SHA1> \
  --delete \
  --confirm
```

### What deletion must remove

After confirmation, delete all archive data associated with that SHA-1:
- `original_files/<SHA1>.*` (original file, any extension)
- `json_extractions/<SHA1>.json`
- the row in `files`
- all rows in typed tables where `file_sha1 = <SHA1>`

### Confirmation policy

- Never delete immediately after receiving the SHA-1.
- Always show the file summary first.
- Always require an explicit user confirmation message before running `--delete --confirm`.
- If the SHA-1 is not found, tell the user that no archived file exists for that hash and do not ask for confirmation.

---

## Quick Start

```bash
# In-session processing — no setup beyond BASE_PATH.
# Attach a file in chat or give Claude a file path.

# Query the database
python scripts/query_db.py \
  --base-path ~/medical-archive \
  --sql "SELECT original_filename, document_type, brief_description FROM files" \
  --format table

# Export all tables to CSV
python scripts/export_csv.py \
  --base-path ~/medical-archive \
  --output-dir ~/medical-export

# Inspect a file before deleting it
python scripts/delete_from_archive.py \
  --base-path ~/medical-archive \
  --sha1 <SHA1>

# Delete a file after confirmation
python scripts/delete_from_archive.py \
  --base-path ~/medical-archive \
  --sha1 <SHA1> \
  --delete \
  --confirm
```

---

## SQLite Schema Reference

### `files` — every imported file
| Column | Type | Notes |
|--------|------|-------|
| `sha1` | TEXT PK | SHA-1 of original file |
| `original_filename` | TEXT | Filename as provided |
| `import_timestamp` | TEXT | ISO 8601 |
| `document_type` | TEXT | lab_result / doctor_visit / imaging_study / discharge_summary / prescription / vaccination / unknown |
| `brief_description` | TEXT | 1–2 sentence summary |
| `language` | TEXT | ru / en / mixed |

### `lab_events` — one row per lab visit
| Column | Type |
|--------|------|
| `id` | INTEGER PK |
| `file_sha1` | TEXT FK |
| `collection_date`, `patient_full_name`, `patient_dob` | TEXT |
| `laboratory_name`, `laboratory_address`, `laboratory_city` | TEXT |
| `ordering_doctor`, `performing_doctor`, `report_date` | TEXT |
| `total_indicators_count` | INTEGER |
| `notes` | TEXT |

### `lab_indicators` — one row per test result
| Column | Type |
|--------|------|
| `id` | INTEGER PK |
| `lab_event_id` | INTEGER FK |
| `file_sha1` | TEXT FK |
| `collection_date` | TEXT |
| `indicator_name` | TEXT | original language |
| `indicator_name_en` | TEXT | English translation |
| `value_raw` | TEXT | e.g. `">5.0"`, `"not detected"` |
| `value_numeric` | REAL | null if not parseable |
| `unit` | TEXT |
| `ref_range_low`, `ref_range_high` | REAL |
| `ref_range_text` | TEXT | as printed |
| `ref_range_notes` | TEXT |
| `range_status` | TEXT | normal / above / below / null |
| `flag` | TEXT | H / L / ! / * |
| `method`, `other_notes` | TEXT |

### `doctor_visits`
`visit_date`, `doctor_full_name`, `doctor_specialty`, `clinic_name`, `clinic_address`,
`chief_complaint`, `anamnesis`, `objective_findings`, `diagnosis_main`, `diagnosis_icd`,
`diagnosis_secondary`, `treatment_plan`, `medications_prescribed`, `recommendations`,
`next_visit_date`, `notes`

### `imaging_studies`
`study_date`, `study_type` (MRI/CT/ultrasound/X-ray/ECG/EEG/endoscopy/…),
`body_region`, `referring_doctor`, `performing_doctor`, `radiologist`, `clinic_name`,
`equipment_model`, `protocol`, `contrast_used`, `description`, `conclusion`,
`recommendations`, `notes`

### `discharge_summaries`
`admission_date`, `discharge_date`, `ward`, `hospital_name`, `attending_doctor`,
`admission_reason`, `diagnosis_on_admission`, `diagnosis_on_discharge`, `diagnosis_icd`,
`procedures_performed`, `treatment_summary`, `discharge_condition`, `discharge_medications`,
`follow_up_instructions`, `notes`

### `prescriptions`
`prescription_date`, `doctor_full_name`, `doctor_specialty`, `clinic_name`,
`medication_name`, `dosage`, `form`, `frequency`, `duration`, `instructions`,
`prescription_number`, `notes`

### `vaccinations`
`vaccination_date`, `vaccine_name`, `disease_targeted`, `manufacturer`, `batch_number`,
`dose_number`, `clinic_name`, `administering_doctor`, `next_dose_date`, `notes`

---

## Configuration

| Variable | Required | Description |
|----------|----------|-------------|
| `BASE_PATH` | Optional | Fallback archive path used when the current working directory is not itself an archive root. Can also be passed per command via `--base-path`. |

Copy `.env.example` to `.env` and set `BASE_PATH` if desired.

**Multiple archives.** You can keep several archives on the same machine (one per
person). To work with a non-default archive, either `cd` into its root directory
(see **Choosing Which Archive To Use**) or pass `--base-path` explicitly. The
`.env` `BASE_PATH` is only used as a fallback when the working directory is not an
archive root.

---

## Error Handling

- **Duplicate**: `{"status": "duplicate", "imported_on": "..."}` — no files modified.
- **Patient mismatch**: `{"status": "patient_mismatch", "archive_patient_names": [...], "file_patient_name": "...", "owner_file_present": true|false}` — no files modified. The file belongs to a different person than the active archive's owner. Surface the mismatch prompt from **Archive Owner Identity**; the preferred retry is `--add-alias` (when `owner_file_present: true` and the user confirms it's the same person), not `--allow-patient-mismatch`.
- **No owner file when alias requested**: `{"status": "no_owner_file", ...}` — `--add-alias` was used against an archive with no `archive_owner.json` yet. Ingest a file with a known patient name first so the owner file can be auto-created.
- **Owner file created**: a normal `status: "ingested"` result may additionally carry `"owner_file_created": true` and an `"owner"` object — surface the owner-identified announcement to the user (see Archive Owner Identity → Auto-creation).
- **No content extracted**: status `error`, message about corrupted/scanned file.
- **errors.log**: one line per failure: `<timestamp> | <sha1> | <filename> | <error>`
- **Unknown document type**: `document_type = "unknown"` in DB; ask user to clarify.

**Common fixes:**

| Error | Fix |
|-------|-----|
| `No text extracted` | Re-export or send a higher-resolution version |
| `Database not found` | Run `python scripts/init_db.py --base-path <path>` |

---

## Privacy & Security

- **Local storage**: all files and extracted data are stored in the archive folder you specify.
- **Session processing**: document content passes through Anthropic's infrastructure as part
  of the Claude session — the same as any conversation in Claude Code.
- **No cloud sync**: original_files, json_extractions, and structured_database stay local
  unless you explicitly use a cloud connector.
- **Backup**: this skill does not back up data. Set up your own backup of BASE_PATH.
- Do not use this skill for third-party medical records without the subject's informed consent.

---

## Installation

```bash
# One-line install (no git required)
curl -fsSL https://raw.githubusercontent.com/romahakov/personal-health-record/main/install.sh | bash

# Or with git
git clone https://github.com/romahakov/personal-health-record \
  ~/.claude/skills/personal-health-record
```

---

**If BASE_PATH is not set up yet:**
> "You haven't set up an archive yet. It only takes a second — where should I
> store your medical files? (e.g. `/Users/you/medical-archive`)"
> Then proceed with hashing, deduplication, extraction, and saving.

### User uploads a file without asking for analysis

> "I see you've uploaded what looks like a medical document. Would you like me to
> first check whether it's already in your archive, and then process and save only the files that are not already there through your Personal Health Record skill? It'll be saved to your archive
> and you can reference it in future questions without re-uploading.
> If you haven't set up the skill yet, I can do that now — it only takes a minute."
