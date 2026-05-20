# Email Preprocessing Pipeline

A multi-layer email body cleaning pipeline that strips signatures, disclaimers, reply-chain headers, and HTML boilerplate from raw email bodies — producing clean, readable plain text suitable for downstream NLP, search, or archival use.

---

## Table of Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Input Format](#input-format)
- [Output Format](#output-format)
- [How to Run](#how-to-run)
- [CLI Options](#cli-options)
- [How It Works — The 8-Layer Pipeline](#how-it-works--the-8-layer-pipeline)
- [Examples](#examples)
- [Notes and Edge Cases](#notes-and-edge-cases)

---

## Requirements

- Python 3.8+
- `beautifulsoup4` — HTML parsing
- `lxml` — HTML parser backend
- `html2text` — HTML-to-Markdown conversion

---

## Installation

```bash
pip install beautifulsoup4 lxml html2text
```

---

## Input Format

The script expects a **JSON file** where each key is a unique email ID and each value is the raw email body string (HTML or plain text).

```json
{
  "email_001": "<html><body><p>Hi team,</p><p>Please review the doc.</p><div class='gmail_signature'>John Doe | john@example.com</div></body></html>",
  "email_002": "Hey,\nCan we meet tomorrow?\nThanks,\nSarah\n--\nSarah Jones | VP Sales | sarah@acme.com",
  "email_003": "On Apr 7, 2026, Paul <paul@example.com> wrote:\n> Let's sync up.\n\nSure, works for me."
}
```

**Key points:**
- Email body values can be raw HTML or plain text — the script auto-detects which.
- Email IDs can be any string (UUIDs, message IDs, custom keys, etc.).
- The file must be UTF-8 encoded.

**Default input filename:** `aviso_logic_monitor_email_eid_level.json` (in the current working directory). You can override this with a positional argument.

---

## Output Format

Running the script produces up to three outputs:

### 1. `<input_stem>_clean.json`
A JSON file with the same keys as the input, but values replaced by cleaned plain text.

```json
{
  "email_001": "Hi team,\n\nPlease review the doc.",
  "email_002": "Hey,\n\nCan we meet tomorrow?",
  "email_003": "--- Reply 1 ---\n\nSure, works for me.\n\n--- Reply 2 (paul@example.com) ---\n\nLet's sync up."
}
```

Emails that are empty after cleaning have an empty string `""` as their value.

### 2. `<input_stem>_clean.md` *(unless `--no-md` is passed)*
A human-readable Markdown report containing:
- Summary statistics (total emails, non-empty count, avg/min/max cleaned length)
- A linked table of contents
- All cleaned email bodies, each under its own heading with its ID

### 3. `<input_stem>_clean_emails/` folder *(unless `--no-md` is passed)*
One individual `.md` file per non-empty email, named after a sanitised version of the email ID. Useful for viewing or indexing emails one at a time.

---

## How to Run

### Basic usage (process all emails)
```bash
python email_preprocessing.py emails.json
```

### Specify a custom output path
```bash
python email_preprocessing.py emails.json -o cleaned_emails.json
```

### Process all emails, skip Markdown output
```bash
python email_preprocessing.py emails.json --no-md
```

### Strip reply chains (keep only the latest message)
```bash
python email_preprocessing.py emails.json --truncate
```

### Preview the first 5 cleaned results in the terminal
```bash
python email_preprocessing.py emails.json --sample 5
```

### Clean and print a single email by ID
```bash
python email_preprocessing.py emails.json --single "email_001"
```

### Combine flags
```bash
python email_preprocessing.py emails.json -o out.json --truncate --sample 3 --no-md
```

---

## CLI Options

| Argument | Type | Default | Description |
|---|---|---|---|
| `input` | positional | `aviso_logic_monitor_email_eid_level.json` | Path to the input JSON file |
| `-o` / `--output` | string | `<input_stem>_clean.json` | Custom path for the output JSON file |
| `--no-md` | flag | off | Skip Markdown report and per-email `.md` file generation |
| `--truncate` | flag | off | Remove older reply chains; keep only the newest message |
| `--sample N` | integer | 0 | Print N sample cleaned emails to the terminal after processing |
| `--single EMAIL_ID` | string | — | Clean and print a single email by its ID, then exit |

---

## How It Works — The 8-Layer Pipeline

Each email body passes through the `clean_email_body()` function, which applies the following layers in order.

### Layer 0 — HTML Structural Removal
*Applies only to HTML emails.*

The raw HTML is parsed with BeautifulSoup (`lxml` backend). The following are removed before any text is extracted:

- HTML comments
- Tags with known **signature CSS classes** (e.g. `gmail_signature`, `protonmail_signature_block`, `Signature`, `AppleMailSignature`, and more across Gmail, Outlook, Yahoo, Apple Mail, Thunderbird, and ProtonMail)
- Tags with known **quote CSS classes** (e.g. `gmail_quote`, `yahoo_quoted`, `OutlookMessageHeader`) — only when `--truncate` is used
- `<blockquote>` elements — only when `--truncate` is used
- Non-content tags: `<script>`, `<style>`, `<head>`, `<meta>`, `<link>`, `<noscript>`, `<img>`
- Presentation attributes: `style`, `class`, `id`, `align`, `bgcolor`, `width`, `height`, etc.
- Inline wrapper tags (`<span>`, `<font>`) are unwrapped, preserving their text content

### Layer 1 — HTML to Markdown Conversion
*Applies only to HTML emails.*

The cleaned HTML is converted to Markdown using `html2text`. Links are preserved inline; images are ignored; tables are kept. This gives a structured plain-text representation that later layers can process line by line.

### Pre-layer — Unicode Normalisation and Escape Cleanup
Applied to all emails (HTML or plain text) after Layer 1:

- Special Unicode characters are replaced with ASCII equivalents (e.g. `\u2019` → `'`, `\u2013` → `-`, `\u00a0` → space)
- NFKD normalisation is applied
- Control characters are removed
- Literal escape sequences (`\r\n`, `\uXXXX` as text strings) are decoded or removed
- `mailto:` links and broken empty Markdown links are cleaned

### Layer 2 — Reply-Chain Truncation
*Only active when `--truncate` is passed.*

The text is scanned line by line. When a **reply trigger** is found, everything from that line onward is discarded. Triggers include:

- `On <date>, <name> wrote:` (Gmail/Apple Mail style)
- `--- Original Message ---` / `--- Forwarded Message ---`
- Outlook-style `From: X\nSent:` headers
- International headers (`De:`, `Von:`, `Da:`)
- Lines beginning with `>` (quoted text)
- Signature separators (`--`)
- Meeting/calendar invitation lines (Zoom, Google Meet, Teams, etc.)

When `--truncate` is **not** used (default), reply chains are preserved and annotated with numbered banners (see Layer 7 below).

### Layer 3 — Disclaimer Removal
Legal and confidentiality disclaimers are removed line by line using two sets of patterns:

- **Trigger patterns**: lines that open a disclaimer block (e.g. `Disclaimer:`, `Confidentiality Notice:`, `This email is confidential`, `If you are not the intended recipient...`)
- **Vocabulary patterns**: words that indicate a line is still part of an active disclaimer block (e.g. `confidential`, `unauthorized`, `prohibited`, `please delete`, `intended recipient`)

Once a trigger is found, subsequent lines are dropped until a blank line or a line with no legal vocabulary is encountered. Content before and after inline disclaimers is preserved.

### Layer 4a — Salutation and Auto-Footer Removal
Email sign-offs and device footers are stripped:

- **Salutation patterns**: `Best regards,`, `Thanks,`, `Kind regards,`, `Cheers,`, `Looking forward to hearing from you`, `Have a great day`, `Thank you for your time`, and dozens of variants
- **Auto-footers**: `Sent from my iPhone`, `Get Outlook for iOS`, `Sent from ProtonMail`, etc.

Once a salutation or auto-footer is detected, the line is dropped along with any immediately following contact lines (phone, email, URL).

### Layer 5 — Line Noise Removal
Cosmetic and structural noise is cleaned:

- Trailing pipe characters (`|`) stripped from line ends
- Residual HTML tags (`<br>`, `</div>`, etc.) stripped from plain-text context
- HTML entities (`&amp;`, `&#160;`, `&#x2019;`, etc.) replaced with spaces

Additionally, these line types are dropped entirely in later whitespace cleanup:
- Pure separator lines (`---`, `===`, `***`, box-drawing characters)
- Pipe-only table artifact lines
- Hashtag-only lines
- Empty Markdown link lines (`[](url)`)
- Empty bold/italic markers (`**`, `***`)

### Layer 6 — Whitespace Collapse
- Consecutive blank lines are collapsed to a maximum of two
- Trailing spaces are removed from each line
- The result is stripped of leading/trailing whitespace
- Results shorter than 10 characters after all cleaning are returned as empty strings

### Layer 7 — Reply Header Processing and Chain Annotation
*Only active when reply history is kept (default, without `--truncate`).*

Email thread headers (`From:`, `To:`, `Sent:`, `Subject:`, `Date:`, and international equivalents) are detected and temporarily replaced with placeholders. Sender email addresses are extracted from `From:` fields. After inline signatures are removed (Layer 8), placeholders are replaced with human-readable reply banners:

```
--- Reply 1 (alice@example.com) ---

<body of the first reply>

--- Reply 2 (bob@example.com) ---

<body of the second reply>
```

Quote-level prefixes (`>`, `>>`) are stripped from all lines, giving a flat, clean text feed.

### Layer 8 — Inline Signature Removal
*Only active when reply history is kept (default).*

This layer surgically detects and removes signature blocks that appear **in the middle of a thread** (between reply banners). Each line is classified as one of: `SALUTATION`, `CONTACT`, `NAME_TITLE`, `SIGN_OFF`, `EMPTY`, or `OTHER`.

Three removal phases run in order:

1. **Contact-based signatures**: Any `CONTACT` line (phone, email, URL, job title, company suffix) anchors a signature block. Up to 3 preceding lines of `NAME_TITLE`/`SALUTATION` and 2 following lines of `CONTACT`/`NAME_TITLE` are included and removed.
2. **Salutation-based sign-offs**: A `SALUTATION` line followed by 1–2 `NAME_TITLE` lines (e.g. `Thanks,\nT.J.`) is removed.
3. **Hyphen sign-offs**: Short lines starting with `-` followed by a name (e.g. `-Gwen`) are removed.

Placeholder lines are never removed in this phase, ensuring reply banners survive.

---

## Examples

### Input
```json
{
  "msg_42": "Hi Alice,\n\nPlease find the report attached.\n\nBest regards,\nBob Smith\nSenior Engineer | Acme Corp\nbob@acme.com | +1 415 555 0100\n\n---\nThis email is confidential. If you are not the intended recipient, please delete it immediately."
}
```

### Output (`--truncate` not used)
```json
{
  "msg_42": "--- Reply 1 ---\n\nHi Alice,\n\nPlease find the report attached."
}
```

### Output with `--truncate`
*(same result here since there is no quoted reply chain)*
```json
{
  "msg_42": "Hi Alice,\n\nPlease find the report attached."
}
```

---

## Notes and Edge Cases

- **HTML auto-detection**: The script detects HTML by looking for tags like `<html>`, `<body>`, `<div>`, `<p>`, `<table>`, `<span>`, `<br>`, `<a>`. Plain-text emails skip Layers 0 and 1 entirely.
- **Short results**: Any email body that is fewer than 10 characters after all cleaning is returned as an empty string and counted as "skipped" in the report.
- **Filename safety**: Per-email `.md` files are named using a sanitised version of the email ID — characters like `<`, `>`, `:`, `/`, `\`, `@`, `?`, `*` are replaced with `_`. IDs longer than 100 characters are truncated.
- **Multilingual headers**: Reply headers in French (`De:`, `Envoyé:`, `Objet:`), German (`Von:`, `Gesendet:`, `Betreff:`), and Spanish/Portuguese (`Enviado:`, `Asunto:`, `Fecha:`) are recognised and processed correctly.
- **Thread depth**: When preserving history, quote depth (`>` prefix count) is used to detect nesting level transitions and assign reply banners in chronological order.
- **Encoding**: All file I/O uses UTF-8. Unicode characters in email bodies are normalised before processing.

---

---

# Email Thread Grouper

Groups raw emails into conversation threads using body-text similarity — no subject line or thread ID required. Two similarity approaches (TF-IDF cosine and Jaccard) are evaluated against each other on the corpus, and the better-performing one is used automatically.

---

## Table of Contents

- [Requirements](#requirements-1)
- [Installation](#installation-1)
- [Configuration](#configuration)
- [Input Format](#input-format-1)
- [Output Format](#output-format-1)
- [How to Run](#how-to-run-1)
- [How It Works — The 5-Step Pipeline](#how-it-works--the-5-step-pipeline)
- [Example](#example)
- [Notes and Edge Cases](#notes-and-edge-cases-1)

---

## Requirements

- Python 3.8+
- `pandas` — DataFrame operations and CSV output
- `numpy` — matrix computation
- `beautifulsoup4` — HTML-to-text extraction
- `scikit-learn` — TF-IDF vectorisation and cosine similarity
- `python-dateutil` — fuzzy date parsing

---

## Installation

```bash
pip install pandas numpy beautifulsoup4 scikit-learn python-dateutil
```

---

## Configuration

At the top of `thread_grouper.py`, three constants control behaviour:

| Constant | Default | Description |
|---|---|---|
| `JSON_FILE` | `aviso_logic_monitor_email_eid_level.json` | Input filename (resolved relative to the script's directory) |
| `TFIDF_THRESHOLD` | `0.50` | Cosine similarity score above which two emails are considered the same thread |
| `JACCARD_THRESHOLD` | `0.35` | Jaccard similarity score threshold for the same purpose |

Edit these directly in the file before running. There are no CLI arguments.

---

## Input Format

A **JSON file** where each key is a unique email ID (`eid`) and each value is the raw email body (HTML or plain text). Two structures are accepted:

**Dict format (primary):**
```json
{
  "eid_001": "<html><body>...</body></html>",
  "eid_002": "<html><body>...</body></html>"
}
```

**List-of-dicts format (also supported):**
```json
[
  {"eid": "eid_001", "raw_body": "<html>...</html>"},
  {"id": "eid_002", "body": "<html>...</html>"}
]
```

Accepted key names for list items: `eid` / `id` / `message_id` for the ID, and `raw_body` / `body` / `html` for the body.

---

## Output Format

All outputs are written to the **same directory as the script**. Three files are produced:

### 1. `threaded_emails_rich_v2.csv`
One row per email with all extracted metadata and the assigned thread ID.

| Column | Description |
|---|---|
| `eid` | Original email ID from the input JSON |
| `thread_id` | Synthetic thread identifier (`thread_<sha256_12>`) |
| `grouping_method` | `body_tfidf`, `body_jaccard`, or `singleton` |
| `from` | Extracted sender email address |
| `to` | Extracted recipient email address(es) |
| `cc` | Extracted CC email address(es) |
| `subject` | Extracted subject line |
| `date` | Extracted date string |
| `timestamp` | Parsed date as epoch milliseconds |
| `reply_depth` | Number of reply levels (blockquotes / "wrote:" markers) |
| `participant_count` | Number of unique email addresses found in the body |
| `participants_str` | Semicolon-separated list of all participant addresses |
| `body_text` | Plain text extracted from the HTML body |

### 2. `thread_summary.json`
One entry per thread with aggregated metadata.

```json
[
  {
    "thread_id": "thread_a3f9c12b4e11",
    "constituent_eids": ["eid_001", "eid_007", "eid_023"],
    "participant_list": ["alice@example.com", "bob@example.com"],
    "timestamp_range": { "earliest": 1743000000000, "latest": 1743100000000 },
    "email_count": 3,
    "grouping_method": "body_tfidf"
  }
]
```

### 3. `thread_report.md`
A human-readable Markdown report containing:
- EID deduplication summary
- Thread grouping results (total threads, multi-email vs singleton counts, avg/max thread size)
- Side-by-side comparison of TF-IDF vs Jaccard approaches with the rationale for the chosen method
- Top 10 largest threads by email count
- Output schema reference
- Validation metrics (avg intra-thread cohesion, percentage of emails successfully grouped)

---

## How to Run

Since there are no CLI arguments, simply set `JSON_FILE` at the top of the script to your input filename, then run:

```bash
python thread_grouper.py
```

The script resolves the input file path relative to its own directory, so you can run it from anywhere as long as the JSON file sits next to the script, or you set the full absolute path in `JSON_FILE`.

**Example with a different input file:**

Edit line 20 in `thread_grouper.py`:
```python
JSON_FILE = "my_emails.json"
```
Then run:
```bash
python thread_grouper.py
```

---

## How It Works — The 5-Step Pipeline

### Step 1 — Load, Parse, and Deduplicate

The JSON file is loaded and converted into a Pandas DataFrame. For each email, the following metadata is extracted directly from the raw HTML body using regex patterns:

- **Sender** (`from`): matched from `On <date>, <name> <email> wrote:`, `From: <email>`, or the first `mailto:` link
- **Recipients** (`to`): matched from `To: ...` header lines
- **CC** (`cc`): matched from `Cc: ...` header lines
- **Subject**: matched from `Subject: ...` header lines
- **Date**: matched from `On Mon, Mar 30, 2026 at 4:37 PM` or `Date: ...` patterns, then parsed to epoch milliseconds using `dateutil`
- **Reply depth**: counted from the number of `wrote:` occurrences or `<blockquote>` tags — whichever is greater

After metadata extraction, exact duplicate `eid` values are dropped (keeping the first occurrence). The deduplication count is recorded for the final report.

### Step 2 — HTML to Plain Text

Each raw HTML body is parsed with BeautifulSoup (`html.parser` backend) and converted to plain text using `get_text(separator=" ", strip=True)`. If parsing fails, a simple regex tag-strip is used as fallback. The resulting `body_text` column is used for all similarity calculations.

### Step 3 — Similarity Matrix Computation (Both Approaches)

Two pairwise N×N similarity matrices are computed over all emails:

**TF-IDF Cosine Similarity**
- The `body_text` corpus is vectorised using `TfidfVectorizer` with unigrams and bigrams (`ngram_range=(1,2)`), a 5,000-feature vocabulary cap, English stop-word removal, and `max_df=0.95` to suppress near-universal terms.
- Pairwise cosine similarity is computed using scikit-learn's `cosine_similarity`.
- Threshold: `0.50` (configurable via `TFIDF_THRESHOLD`).

**Jaccard Similarity**
- Each body is tokenised into a set of lowercase words of 3+ characters.
- Pairwise Jaccard similarity = |A ∩ B| / |A ∪ B| is computed in a nested loop.
- Threshold: `0.35` (configurable via `JACCARD_THRESHOLD`).

### Step 4 — Clustering via Connected Components

For each similarity matrix, a **Union-Find (disjoint set)** algorithm groups emails into clusters:

- For every pair (i, j) where `similarity[i][j] >= threshold`, i and j are merged into the same set.
- The result is a set of connected components — each component becomes one thread.
- Emails that share no sufficiently similar pair end up as **singletons** (their own single-email thread).

Both approaches are evaluated with the following metrics: total threads, multi-email thread count, singleton count, average thread size, maximum thread size, and **average intra-thread cohesion** (mean pairwise similarity within each multi-email thread).

**Method selection**: TF-IDF is chosen by default. Jaccard is chosen instead only if TF-IDF's average cohesion is less than 80% of Jaccard's cohesion, meaning Jaccard produced tighter, more coherent clusters on this particular corpus.

### Step 5 — Thread ID Assignment and Output Generation

Each cluster is assigned a deterministic **synthetic thread ID**:
- The member `eid` values are sorted and joined with `|`.
- A 12-character SHA-256 hex digest is computed from that string.
- The thread ID becomes `thread_<digest>` — stable across runs as long as membership doesn't change.

The `grouping_method` column is then set to `body_tfidf` or `body_jaccard` for multi-email threads, or `singleton` for lone emails. Pandas `groupby("thread_id")` aggregates participant lists, timestamp ranges, and email counts into the `thread_summary.json`. The enriched DataFrame is written to `threaded_emails_rich_v2.csv`, and the full comparison report is written to `thread_report.md`.

**Validation** runs after output generation to assert: every email has exactly one `thread_id`, no duplicate `eid` values exist in the output, and all thread IDs are non-empty strings.

---

## Example

**Input (`aviso_logic_monitor_email_eid_level.json`):**
```json
{
  "msg_01": "<html><body><p>Hi Bob, can you review the Q3 proposal?</p></body></html>",
  "msg_02": "<html><body><p>Hi Alice, I reviewed the Q3 proposal. Looks good.</p><blockquote>can you review the Q3 proposal</blockquote></body></html>",
  "msg_03": "<html><body><p>Reminder: team standup at 9am tomorrow.</p></body></html>"
}
```

**`thread_summary.json` (excerpt):**
```json
[
  {
    "thread_id": "thread_a1b2c3d4e5f6",
    "constituent_eids": ["msg_01", "msg_02"],
    "participant_list": [],
    "email_count": 2,
    "grouping_method": "body_tfidf"
  },
  {
    "thread_id": "thread_9f8e7d6c5b4a",
    "constituent_eids": ["msg_03"],
    "email_count": 1,
    "grouping_method": "singleton"
  }
]
```

`msg_01` and `msg_02` are grouped together because `msg_02`'s body quotes `msg_01`, making their TF-IDF cosine similarity exceed 0.50. `msg_03` has no similar counterpart and becomes a singleton.

---

## Notes and Edge Cases

- **No CLI arguments**: All configuration is via constants at the top of the script. The input filename, TF-IDF threshold, and Jaccard threshold must be edited directly in the file.
- **Output location**: All three output files are always written to the script's own directory, regardless of where the script is run from.
- **Scalability**: The Jaccard matrix uses a Python nested loop (O(N²)), which can be slow for corpora larger than ~5,000 emails. TF-IDF uses scikit-learn's sparse matrix operations and scales much better.
- **Thread ID stability**: Thread IDs are deterministic — re-running on the same input produces the same IDs. However, adding or removing emails from the corpus will change cluster membership and therefore regenerate thread IDs.
- **Quoted reply chains**: Because reply emails typically quote the earlier message in full, the body-text overlap between a reply and its parent is high, making similarity-based grouping particularly effective for email threads.
- **Empty bodies**: Emails with no extractable text (empty HTML, images-only) will have zero similarity to all other emails and will become singletons.
- **Dual JSON input formats**: Both `{eid: body}` dicts and lists of `{eid, raw_body}` objects are supported, with fallback key-name detection for list items.

---

---

# Automation / Bulk Email Filter

A hybrid ML pipeline that classifies emails as **genuine** (human-written) or **automated/bulk** (out-of-office replies, newsletters, system notifications, AI-generated content). It combines weak rule-based labeling with a trained TF-IDF + Logistic Regression classifier, applies a conservative dual-gate decision at inference time, and saves the results into separate output files for downstream use.

---

## Table of Contents

- [Requirements](#requirements-2)
- [Installation](#installation-2)
- [Configuration](#configuration-1)
- [Input Format](#input-format-2)
- [Output Format](#output-format-2)
- [How to Run](#how-to-run-2)
- [How It Works — The Pipeline](#how-it-works--the-pipeline)
- [Example](#example-1)
- [Notes and Edge Cases](#notes-and-edge-cases-2)

---

## Requirements

- Python 3.8+
- `pandas` — DataFrame operations and CSV output
- `numpy` — array operations
- `beautifulsoup4` — HTML stripping during text cleaning
- `scikit-learn` — TF-IDF vectorisation, Logistic Regression, metrics
- `scipy` — sparse matrix stacking (`hstack`)
- `joblib` — model and vectorizer serialisation

---

## Installation

```bash
pip install pandas numpy beautifulsoup4 scikit-learn scipy joblib
```

---

## Configuration

Six constants at the top of `automation_bulk.py` control all paths and the filtering threshold. Edit them directly before running — there are no CLI arguments.

| Constant | Default | Description |
|---|---|---|
| `JSON_PATH` | `aviso_logic_monitor_email_eid_level_clean.json` | Input JSON file of cleaned email bodies |
| `LABELED_CSV_PATH` | `labeled_emails.csv` | Where the weak-labeled training dataset is saved |
| `FILTERED_OUTPUT_PATH` | `filtered_bulk_emails.json` | (Legacy constant — actual paths are set inside `save_outputs`) |
| `GENUINE_OUTPUT_PATH` | `genuine_emails.csv` | (Legacy constant — actual paths are set inside `save_outputs`) |
| `MODEL_PATH` | `email_filter_model.pkl` | Where the trained Logistic Regression model is saved |
| `VECTORIZER_PATH` | `tfidf_vectorizer.pkl` | Where the fitted TF-IDF vectorizer is saved |
| `THRESHOLD` | `0.95` | Minimum ML confidence to classify an email as automated |

> **Note on output paths**: The four output files (CSV, JSON, two TXT files) are written to hardcoded absolute paths inside `save_outputs()`. Before running, update those four path strings in the function to match your local directory.

---

## Input Format

A **JSON file** (the `JSON_PATH` constant) where each key is a unique email ID and each value is the cleaned email body text. This is intended to be the output of `email_preprocessing.py`.

```json
{
  "email_001": "Hi team, please review the attached proposal before Friday.",
  "email_002": "Out of Office: I am currently out of the office and will respond upon my return.",
  "email_003": "Unsubscribe from this newsletter | Manage preferences | View in browser"
}
```

---

## Output Format

Running the pipeline produces six files:

### 1. `labeled_emails.csv`
The full dataset with weak labels assigned by the heuristic rules. Used as training data for the classifier.

| Column | Description |
|---|---|
| `email_id` | Original email ID from the input JSON |
| `text` | Raw email body text |
| `label` | `1` = automated/bulk, `0` = genuine (assigned by weak labeler) |
| `category` | Detected category: `ooo`, `newsletter`, `system`, `ai_generated`, or `genuine` |

### 2. `email_filter_model.pkl`
The serialised trained Logistic Regression model. Can be loaded with `joblib.load()` for future inference without retraining.

### 3. `tfidf_vectorizer.pkl`
The serialised fitted TF-IDF vectorizer. Must be paired with `email_filter_model.pkl` for inference.

### 4. `genuine_emails.csv`
All emails the pipeline decided to keep (predicted genuine). Contains: `email_id`, `text`, `clean_text`, `automation_probability`, `is_filtered`.

### 5. `filtered_emails.json`
All emails classified as automated/bulk, saved as a JSON array of records with the same columns as the CSV above.

### 6. `genuine_email_bodies.txt` / `filtered_email_bodies.txt`
Human-readable text dumps of the kept and filtered emails respectively. Each email is separated by an 80-character `=` line with its ID printed as a header, making them easy to manually review.

---

## How to Run

1. Update `JSON_PATH` at the top of the script to point to your cleaned email JSON file.
2. Update the four hardcoded output paths inside `save_outputs()` to your desired output directory.
3. Run:

```bash
python automation_bulk.py
```

The script trains a new model from scratch on every run. If you want to skip retraining and run inference only using a saved model, load `email_filter_model.pkl` and `tfidf_vectorizer.pkl` with `joblib.load()` and call `classify_emails()` directly.

---

## How It Works — The Pipeline

### Stage 1 — Load Data

`load_json_dataset()` reads the input JSON and converts it to a Pandas DataFrame with two columns: `email_id` and `text`. Both dict (`{id: body}`) format is expected.

### Stage 2 — Text Cleaning

`clean_email_text()` applies a sequence of normalisation steps to each email body before any ML processing:

- HTML tags are stripped using BeautifulSoup (`html.parser`)
- Text is lowercased
- URLs are replaced with the token `URL`
- Email addresses are replaced with the token `EMAIL`
- Reply chain separators (`--- reply N ---`) are removed
- Repeated underscores and `* * *` sequences are removed
- Seven hard-coded disclaimer phrases are removed (e.g. `"confidential - authorized use only"`, `"click here to unsubscribe"`, `"please do not reply"`)
- Excessive whitespace is collapsed to single spaces

### Stage 3 — Weak Labeling

`weak_label()` scans each cleaned email body for keyword patterns to assign a provisional label without any manual annotation. The four categories and their trigger phrases are:

| Category | Example trigger phrases |
|---|---|
| `ooo` | `out of office`, `automatic reply`, `i will respond upon my return`, `replies delayed` |
| `newsletter` | `unsubscribe`, `manage preferences`, `newsletter`, `digest`, `view in browser` |
| `system` | `automated message`, `do not reply`, `delivery failure`, `mailer-daemon`, `generated automatically` |
| `ai_generated` | `generated by ai`, `meeting notes` |

Any email matching at least one phrase in any category is labeled `1` (automated). All others are labeled `0` (genuine). These weak labels are saved to `labeled_emails.csv` and used as training targets for the classifier.

### Stage 4 — Feature Engineering

Two feature sets are built and combined before training and inference:

**TF-IDF features** (sparse matrix, up to 15,000 features):
- Unigrams and bigrams (`ngram_range=(1, 2)`)
- English stop-word removal
- `min_df=2` (ignore very rare terms), `max_df=0.95` (ignore near-universal terms)
- Sublinear TF scaling (`sublinear_tf=True`) to dampen the effect of very frequent terms

**Hand-crafted extra features** (8 dense columns):
- Binary flags: `contains_unsubscribe`, `contains_ooo`, `contains_do_not_reply`, `contains_generated_by_ai`, `contains_meeting_notes`
- Structural: `text_length` (character count), `num_urls` (count of `URL` tokens), `num_reply_markers` (count of `reply` occurrences)

The two feature matrices are concatenated horizontally using `scipy.sparse.hstack` into a single combined feature matrix `X`.

### Stage 5 — Model Training

A **Logistic Regression** classifier is trained on the combined feature matrix:

- `max_iter=3000` to ensure convergence on larger corpora
- `class_weight="balanced"` to compensate for class imbalance (genuine emails typically far outnumber automated ones)
- An 80/20 stratified train/test split is used; the stratification ensures both classes are proportionally represented in both sets

After training, the model is evaluated on the held-out test set. The console prints a full classification report (precision, recall, F1 per class), a confusion matrix, and specifically highlights the number of genuine emails wrongly filtered — the most costly error type. Both the model and vectorizer are saved as `.pkl` files via `joblib`.

### Stage 6 — Inference with Conservative Dual-Gate Logic

At inference time, `classify_emails()` applies a **two-gate decision** for each email to minimise false positives (genuine emails incorrectly filtered):

```
IF   weak_label(email) == 1          → filter it   (strong heuristic match)
ELIF ML_probability >= THRESHOLD     → filter it   (high-confidence ML prediction)
ELSE                                 → keep it     (default: preserve the email)
```

The threshold defaults to `0.95` — only emails where the model is at least 95% confident are automated get filtered by ML alone. Any email that triggers a keyword rule is filtered regardless of the ML score. This conservatism means the filter errs on the side of keeping emails rather than discarding genuine ones.

Each row in the DataFrame gets two new columns: `automation_probability` (raw ML score 0–1) and `is_filtered` (final binary decision).

### Stage 7 — Save Outputs

`save_outputs()` splits the DataFrame by `is_filtered`, then writes the four output files (genuine CSV, filtered JSON, genuine TXT, filtered TXT) to the hardcoded paths. A summary is printed to the console showing how many emails were kept vs filtered.

---

## Example

**Input (`aviso_logic_monitor_email_eid_level_clean.json`):**
```json
{
  "msg_01": "Hi Alice, I've reviewed the proposal and have a few questions.",
  "msg_02": "Out of Office: I am currently out of the office and will have limited access during this time.",
  "msg_03": "Please unsubscribe me from this newsletter. Manage your preferences here."
}
```

**Console output (summary):**
```
========== LOADING DATA ==========
Loaded emails: 3

========== TRAINING MODEL ==========
========== EVALUATION ==========
              precision    recall  f1-score
     Genuine       ...
   Automated       ...

Genuine emails wrongly filtered: 0

========== RESULTS ==========
Genuine emails kept: 1
Automated emails filtered: 2
```

**`genuine_emails.csv`** will contain `msg_01`.
**`filtered_emails.json`** will contain `msg_02` and `msg_03`.

---

## Notes and Edge Cases

- **Hardcoded output paths**: The four output file paths inside `save_outputs()` point to a Windows path (`C:\Users\purni\Desktop\bulkauto\`). Update these to your own directory before running, or the script will fail with a `FileNotFoundError`.
- **No CLI arguments**: All configuration is done by editing constants at the top of the file and paths inside `save_outputs()`.
- **Retrains on every run**: The model is retrained from scratch each time `main()` is called. The weak labels are regenerated from the same keyword rules, so results are deterministic. To reuse a saved model, load the `.pkl` files and call `classify_emails()` directly.
- **Conservative threshold**: The default `THRESHOLD = 0.95` is intentionally high. Lowering it (e.g. to `0.80`) will filter more emails but risks discarding genuine ones. Raising it above `0.95` is not meaningful since the heuristic gate already catches clear cases.
- **`ai_generated` category**: The trigger phrases `"generated by ai"` and `"meeting notes"` are broad. `"meeting notes"` in particular may match genuine emails that include meeting summaries. Review filtered output for false positives in this category.
- **Weak label quality**: The training labels come entirely from keyword matching with no manual review. The model learns to generalise these patterns but is bounded by the quality of the weak labels. Adding manually verified labels via `labeled_emails.csv` (editing `label` column) and feeding them back into training would improve precision.
- **Input is expected to be pre-cleaned**: `JSON_PATH` defaults to `aviso_logic_monitor_email_eid_level_clean.json` — the output of `email_preprocessing.py`. Running on raw HTML bodies still works (the internal `clean_email_text()` strips HTML), but pre-cleaned input gives better feature quality.
