---
name: personal-health-record
description: >
  Medical records assistant for organizing and querying personal health documents
  (lab results, doctor visits, MRI/ultrasound/X-ray/CT/ECG, discharge summaries,
  prescriptions, vaccinations). Use when users mention adding medical PDFs,
  querying health history, tracking lab indicator trends, reviewing past diagnoses,
  searching prescriptions, or organizing any medical paperwork. Trigger on casual
  mentions like "add my MRI results", "when was my last CBC", "show my vaccination
  history", "find all prescriptions from last year", "how has my hemoglobin changed",
  "что показал анализ крови", "добавь результаты анализов". Supports Russian and
  English documents (mixed-language documents are handled correctly).
  Also trigger reactively: after reading and analyzing any PDF in chat that turns
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

## Architecture

```
<BASE_PATH>/
├── original_files/          # Original PDFs, named by SHA-1 hash (never modified)
├── json_extractions/        # Full structured JSON extraction per file
└── structured_database/
    ├── medical.db           # SQLite — 8 typed tables
    └── errors.log           # Ingestion errors
```

Query order: **structured_database first** (fastest) → **json_extractions** if
SQL is insufficient → **original_files** only when the user explicitly asks.

---

## First-Run Onboarding

On first use, greet the user with:

> "Hi! This skill helps you organize and analyze your medical documents.
>
> **original_files** — your original PDFs, stored unchanged (named by SHA-1 hash).
> **json_extractions** — full structured JSON extracted from each file.
> **structured_database** — a local SQLite database with typed tables for each
>   document type (lab results, doctor visits, imaging studies, etc.).
>
> When you ask a question, I always query structured_database first (fastest).
> If that's not enough, I look at json_extractions. I only open original_files
> if you explicitly ask me to.
>
> Let's start — where should I store your medical archive?
> Please give me a folder path, e.g. `/Users/roman/medical-archive`."

After the user provides BASE_PATH, ask:

> "Got it. How would you like to provide files?
> A) Attach PDFs directly in this chat (great for 1–20 files)
> B) Give me a folder path on your computer — I'll read each file in this session
> C) Other source (Google Drive, email, clinic website via MCP)"

Record BASE_PATH and the chosen method for the session.

---

## Operational Modes

| User says | Action | Script |
|-----------|--------|--------|
| PDF attached in chat | in-session ingest | Claude reads + `save_extraction.py` |
| "add folder", "import all PDFs from …" | in-session ingest (multiple files) | Claude reads each + `save_extraction.py` |
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
Before reading or extracting anything from any PDF, compute the SHA-1
for every file in the batch and check whether each hash already exists in the `original_files` folder.
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

If a matching file exists, do not extract that PDF again unless the user explicitly asks to
re-process it with `--force`.

**Step 3 — Read only the files that are not yet archived**
Use the `Read` tool on each file path that is not already present in the archive.
Skip files that already exist.

**Step 4 — Extract structured data (Claude does this inline)**
Apply the extraction schema below and produce a JSON object with the required structure
for each new file.

**Step 5 — Save newly extracted files to the archive immediately**
After extraction, save each newly processed file to the archive in the same run.
Do not pause for a separate confirmation step once the user has asked to process the files.

Use:
```bash
python scripts/save_extraction.py \
  --base-path <BASE_PATH> \
  --pdf-path <path_to_original_pdf> \
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

**If PDF has no readable text:**
> "Couldn't extract text from `{filename}`. It may be corrupted, password-protected,
> or a very low-resolution scan. Please try re-exporting or sending a clearer scan."

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
   - whether the original PDF and JSON extraction currently exist
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
- `original_files/<SHA1>.pdf`
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
# Attach a PDF in chat or give Claude a file path.

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
| `sha1` | TEXT PK | SHA-1 of original PDF |
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
| `BASE_PATH` | Optional | Default archive path (can also pass `--base-path` per command) |

Copy `.env.example` to `.env` and set `BASE_PATH` if desired. The scripts read `.env` automatically.

---

## Error Handling

- **Duplicate**: `{"status": "duplicate", "imported_on": "..."}` — no files modified.
- **No text in PDF**: status `error`, message about corrupted/scanned file.
- **errors.log**: one line per failure: `<timestamp> | <sha1> | <filename> | <error>`
- **Unknown document type**: `document_type = "unknown"` in DB; ask user to clarify.

**Common fixes:**

| Error | Fix |
|-------|-----|
| `No text extracted` | Re-export PDF or scan at higher resolution |
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
pip install -r ~/.claude/skills/personal-health-record/requirements.txt
```

---

**If BASE_PATH is not set up yet:**
> "You haven't set up an archive yet. It only takes a second — where should I
> store your medical files? (e.g. `/Users/roman/medical-archive`)"
> Then proceed with hashing, deduplication, extraction, and saving.

### User uploads a PDF without asking for analysis

> "I see you've uploaded what looks like a medical document. Would you like me to
> first check whether it's already in your archive, and then process and save only the files that are not already there through your Personal Health Record skill? It'll be saved to your archive
> and you can reference it in future questions without re-uploading.
> If you haven't set up the skill yet, I can do that now — it only takes a minute."
