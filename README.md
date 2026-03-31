# DocSplitter

Intelligent multi-document PDF splitter powered by vision AI. Drop a scanned batch of mixed documents — invoices, purchase orders, letters, contracts — and DocSplitter detects the boundaries between them, splits them into individual files, and names each one automatically.

When confidence is high the split happens automatically. When it is low, a human reviewer is presented with a visual editor to adjust the split points before the output files are written.

---

## How it works

1. **Ingest** — a file arrives via folder watcher or HTTP upload
2. **Render** — each page is rendered to an image (150 dpi by default)
3. **Extract** — text is pulled from the PDF layer; OCR fallback (Tesseract) is used when no text layer is present
4. **Analyse** — a vision-capable AI model examines each page in a sliding window (previous + current + next) and decides whether it starts a new document
5. **Split or queue** — if the model's confidence across all boundaries meets the channel threshold the PDF is split automatically; otherwise the job enters the review queue
6. **Output** — split PDFs are written to the output directory with structured filenames; an optional JSON sidecar records provenance metadata

---

## Features

- **Folder watcher** — monitors directories and processes files as they arrive
- **API upload** — HTTP endpoint accepts PDFs directly; job status is polled in real time
- **Visual review UI** — drag-and-drop split editor with page thumbnails; reviewers can add/remove splits and correct document types before approving
- **Drag-and-drop upload UI** — drop files onto channel cards in the browser; watch progress and download results directly
- **Channel configuration** — each channel has its own confidence threshold, document type hints, and split trigger rules
- **Split trigger types** — optionally restrict splitting to specific document types (e.g. split only on `invoice`, keeping attached supporting documents in the same output file)
- **Azure OpenAI + OpenAI + local LLMs** — works with any OpenAI-compatible API including LM Studio and Ollama
- **Admin dashboard** — live job list, review queue with badge count, channel management, system health

---

## Quick start with Docker

**1. Copy and configure the environment file**

```bash
cp .env.sample .env
```

Edit `.env` and set your AI provider credentials (see [Configuration](#configuration) below).

**2. Start the container**

```bash
docker compose up -d
```

**3. Open the admin dashboard**

```
http://localhost:8000
```

**4. Upload a PDF**

Either drop a file into the `./watch/invoices` folder, or go to the **Upload** tab in the dashboard and drag a file onto a channel card.

---

## Configuration

Configuration is layered — later sources override earlier ones:

| Source | Path | Notes |
|--------|------|-------|
| Defaults | `config/default.yaml` | Committed, safe to inspect |
| Local overrides | `config/local.yaml` | Git-ignored; override any key |
| Environment variables | `DOCSPLITTER_*` | Highest priority; used in Docker |

### AI provider

Set these in `.env` (Docker) or `config/local.yaml` (local dev).

**OpenAI**
```env
DOCSPLITTER_AI__API_KEY=sk-your-key-here
DOCSPLITTER_AI__BASE_URL=https://api.openai.com/v1
DOCSPLITTER_AI__MODEL=gpt-4o
```

**Azure OpenAI**
```env
DOCSPLITTER_AI__API_KEY=your-azure-api-key
DOCSPLITTER_AI__BASE_URL=https://your-resource.cognitiveservices.azure.com
DOCSPLITTER_AI__API_VERSION=2025-01-01-preview
DOCSPLITTER_AI__MODEL=gpt-4o
```

**Local LLM (LM Studio / Ollama)**
```env
DOCSPLITTER_AI__API_KEY=no-key
DOCSPLITTER_AI__BASE_URL=http://host.docker.internal:1234/v1
DOCSPLITTER_AI__MODEL=qwen/qwen3-vl-8b
```

> For local models a vision-capable model is required. Qwen3-VL-8B has been tested and works well. Text extraction (PDF layer + OCR) is used to supplement the model's vision for fine text.

### AI tuning options

| Variable | Default | Description |
|----------|---------|-------------|
| `DOCSPLITTER_AI__RENDER_DPI` | `150` | Page image resolution. Higher = more accurate, slower |
| `DOCSPLITTER_AI__IMAGE_FORMAT` | `jpeg` | `jpeg` (smaller) or `png` (higher fidelity) |
| `DOCSPLITTER_AI__IMAGE_QUALITY` | `85` | JPEG quality 0–100 |
| `DOCSPLITTER_AI__IMAGE_DETAIL` | `auto` | `auto`, `low`, or `high`. Use `auto` for best accuracy with GPT-4o |
| `DOCSPLITTER_AI__MAX_TOKENS` | `600` | Max tokens per response |
| `DOCSPLITTER_AI__TIMEOUT_SECONDS` | `60` | Per-call timeout |
| `DOCSPLITTER_AI__MAX_RETRIES` | `3` | Retries on transient errors |

### Channels

Channels define how files are processed. Configure them in the admin UI or in `config/local.yaml`.

```yaml
channels:
  - name: invoices
    type: watcher               # monitors a folder
    path: ./watch/invoices      # folder to watch (must match volume mount)
    output_subdir: invoices
    confidence_threshold: 0.85  # above this → auto-split; below → review queue
    type_hints:                 # helps the model classify document types
      - invoice
      - purchase_order
      - remittance_advice
      - credit_note
    split_trigger_types:        # optional: only split on these types
      - invoice                 # supporting docs are appended to the preceding invoice

  - name: uploads
    type: api                   # accepts HTTP uploads
    output_subdir: uploads
    confidence_threshold: 0.75
```

**Channel types**

| Type | How files arrive |
|------|-----------------|
| `watcher` | Drop files into the watched folder; processed automatically when stable |
| `api` | POST to `/api/v1/ingest/upload?channel=<name>`; also available via the Upload tab in the UI |

**`split_trigger_types`** — when set, a new document boundary is only created when the AI detects a page that both starts a new document *and* has a type in this list. All other new-document pages are appended to the preceding trigger document. Leave empty to split on every detected boundary.

---

## Admin dashboard

Access at `http://localhost:8000`.

| Tab | Purpose |
|-----|---------|
| **Dashboard** | Summary stats, recent jobs |
| **Upload** | Drag files onto API channel cards; real-time progress; download split outputs |
| **Channels** | Create, edit, and delete channels |
| **Review Queue** | Items awaiting human review with confidence below threshold |
| **Jobs** | Full job history with status filter |
| **System** | AI backend and output configuration |

### Review editor

When a job enters the review queue, click **Review** to open the full-screen split editor:

- Thumbnails of every page are shown in a strip
- Coloured borders indicate detected document sections
- Click the **+** handle between pages to add a split; click the red **×** to remove one
- Edit document types for each section in the bar below the strip
- Click a thumbnail to see a full-size page preview
- **Approve & Split** writes the output files; **Reject** discards the job

---

## API

Interactive docs are available at `http://localhost:8000/docs`.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/ingest/upload?channel=<name>` | Upload a PDF; returns `job_id` |
| `GET` | `/api/v1/jobs/{job_id}` | Poll job status and output paths |
| `GET` | `/api/v1/jobs/{job_id}/outputs/{index}` | Download a split output file |
| `GET` | `/api/v1/jobs/{job_id}/download-zip` | Download all outputs as a ZIP |
| `GET` | `/api/v1/jobs/{job_id}/review` | Get the review item for a job |
| `GET` | `/api/v1/review` | List review items (filterable by status) |
| `GET` | `/api/v1/review/{review_id}` | Review item detail with boundaries |
| `GET` | `/api/v1/review/{review_id}/pages/{page}/image` | Rendered page image |
| `PUT` | `/api/v1/review/{review_id}/boundaries` | Update split boundaries |
| `POST` | `/api/v1/review/{review_id}/approve` | Approve and write output files |
| `POST` | `/api/v1/review/{review_id}/reject` | Reject (no output written) |
| `GET` | `/api/v1/channels` | List channels |
| `POST` | `/api/v1/channels` | Create channel |
| `PUT` | `/api/v1/channels/{name}` | Update channel |
| `DELETE` | `/api/v1/channels/{name}` | Delete channel |
| `GET` | `/api/v1/health` | Health check including AI reachability |

**Job statuses**

| Status | Meaning |
|--------|---------|
| `pending` | Queued, not yet started |
| `processing` | Being analysed |
| `auto_split` | Split automatically; output files written |
| `review` | Below confidence threshold; awaiting human review |
| `approved` | Reviewer approved; output files written |
| `rejected` | Reviewer rejected; no output written |
| `failed` | Processing error |

---

## Output files

Split PDFs are written to `./output/<channel>/<subdir>/`. Each file is named using the configured template:

```
{date}_{doc_type}_{doc_index:03d}.pdf
# e.g. 2026-04-01_invoice_001.pdf
```

Available template variables: `{channel}`, `{date}`, `{doc_type}`, `{doc_index}`, `{job_id}`.

When `write_metadata_json: true` (default) a sidecar JSON file is written alongside each PDF recording the source file, page range, confidence score, model used, and job ID.

---

## Local development

Requires Python 3.12+ and [uv](https://github.com/astral-sh/uv).

```bash
# Install dependencies
uv sync

# Copy and edit config
cp config/local.yaml.example config/local.yaml

# Run the server
uv run docsplitter
```

For Tesseract OCR (used as fallback when PDFs have no text layer):

```bash
# macOS
brew install tesseract

# Debian / Ubuntu
apt-get install tesseract-ocr
```

**Run tests**

```bash
uv run pytest
```

---

## Volume mounts (Docker)

| Host path | Container path | Purpose |
|-----------|---------------|---------|
| `./watch` | `/app/watch` | Input folders for watcher channels |
| `./output` | `/app/output` | Split PDF output |
| `./config` | `/app/config` | Config overrides (`local.yaml`) |
| Named volume `docsplitter-db` | `/app/data` | SQLite database (persisted across restarts) |
