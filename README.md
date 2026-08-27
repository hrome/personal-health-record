# Personal Health Record — Claude Code Skill

A skill for [Claude Code](https://claude.ai/code) that helps you organize and analyze your personal medical documents: lab results, doctor visits, imaging studies (MRI, CT, ultrasound, ECG), discharge summaries, prescriptions, and vaccination records.

Supports **Russian and English** documents. All extracted data is stored locally on your machine.

---

## What it does

**Add documents** — share a medical PDF with Claude in any way that's convenient: attach it in chat, give a local file path, or load it from a cloud service via MCP. Claude reads the document, extracts all structured data, and saves it to a local archive.

**Ask questions in plain language:**
- "What was my hemoglobin last January?"
- "Show all diagnoses from the past two years"
- "Have I ever been prescribed metformin?"
- "Show my cholesterol trend"
- "When did I last see a cardiologist?"

**Timeline and trends** — get a chronological view of your medical history or track how any lab indicator changes over time.

**Export** — export your data to CSV at any time.

---

## How it works

Every document goes through three layers stored in a folder of your choice:

```
your-archive/
├── archive_owner.json    # who this archive belongs to (created automatically)
├── original_files/       # original files, stored unchanged
├── json_extractions/     # full structured extraction of each document
└── structured_database/
    ├── medical.db        # SQLite database — fast queries across all documents
    └── errors.log        # one line per failed ingest
```

When you ask a question, Claude queries the database first. No re-reading PDFs on every question.

---

## Requirements

- [Claude Code](https://claude.ai/code) installed
- Python 3.10 or newer (standard library only — no packages to install)

---

## Installation

### Option 1 — One command (no git required)

Open Terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/romahakov/personal-health-record/main/install.sh | bash
```

The script checks your Python version, downloads the skill, and places it in the right
folder. There are no third-party dependencies to install — the skill uses only the
Python standard library.

---

### Option 2 — Download ZIP

1. Click the green **Code** button at the top of this page
2. Choose **Download ZIP**
3. Unzip the downloaded file
4. Rename the folder from `personal-health-record-main` to `personal-health-record`
5. Move the folder to `~/.claude/skills/`
---

### Option 3 — Git clone

```bash
git clone https://github.com/romahakov/personal-health-record \
  ~/.claude/skills/personal-health-record
```

Updates: `git pull` inside `~/.claude/skills/personal-health-record`.

---

## First use

Open Claude Code, start a new conversation, and say:

> "Set up my medical records archive"

Claude will ask where to store your archive and how you'd like to add files, then guide you through the rest.

---

## Adding documents

Share a medical document with Claude in whatever way is most convenient:

- **Attach in chat** — drag a PDF into the message field. Claude reads it directly in the session.
- **Local path** — tell Claude a file or folder path: "Import all PDFs from ~/Downloads/medical-docs". Claude reads each file from disk.
- **Cloud or MCP** — load a file from Google Drive, Dropbox, or any connected MCP server.

Claude extracts all structured data within the same session and saves it to the local archive. No API key required.

---

## Supported document types

| Type | What gets extracted |
|------|---------------------|
| Lab results | Every indicator: name, value, unit, reference range, in/out of range flag |
| Doctor visits | Diagnosis, ICD code, treatment plan, prescriptions, recommendations |
| Imaging studies | MRI, CT, ultrasound, X-ray, ECG, EEG — description, conclusion |
| Discharge summaries | Admission/discharge dates, diagnoses, procedures, discharge medications |
| Prescriptions | Medication, dosage, frequency, duration |
| Vaccinations | Vaccine name, dose number, next dose date |

---

## Privacy

- All extracted data and original files are stored **locally** in the archive folder you choose.
- Document content passes through Anthropic's infrastructure as part of the Claude session — the same as any conversation you have with Claude Code.
- There is no separate batch ingestion API call and no dedicated server-side storage of your records.
- Do not use this skill for other people's medical records without their consent.

---

## Development

Run the test suite (standard library only, no test framework to install):

```bash
python3 -m unittest discover -s tests
```

---

## License

MIT
