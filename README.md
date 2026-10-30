# AdvoHQ repair — local chat application

This is the repaired source for your existing Docker project. No judgments, embeddings or API keys are bundled. The interface is a simple chatbox: no sidebar, marketing copy, subtitles or visible ranking. Settings, court/method/date controls, citations and the judgment reader remain available.

## Install over your current project without losing data

Unzip this repair into a separate folder, for example `~/Downloads/advohq-repair`. Do not replace or delete your existing `~/Downloads/advohq-case-research` folder.

```bash
python3 ~/Downloads/advohq-repair/apply_repair.py \
  ~/Downloads/advohq-case-research --recover-env

cd ~/Downloads/advohq-case-research
docker compose up --build -d --force-recreate api
open http://localhost:8000
```

The installer updates only source/configuration files from an explicit allowlist, backs up changed files under `repair-backups/`, and never replaces an existing `.env`, `imports/`, databases or Docker volumes. If your `.env` is missing, the optional `--recover-env` flag recovers recognised settings from the existing running API container, saves them with owner-only permissions, and does not print keys. Running it again is safe. `--dry-run` lists planned changes without writing. Compose explicitly retains project name `advohq-case-research`, so the existing named `research_data` and `vector_data` volumes are reused. **Do not run `docker compose down -v`** if you want to preserve data. A container rebuild can discard the old container's Hugging Face download cache; imported records and embeddings remain in the named volumes.

## Import every available court judgment file

Version 1.2.0 adds a manifest-driven bulk importer. The 497-document test database came from the 1,000-chunk cap; multiple chunks can belong to one judgment. This command covers **all `_judgments.parquet` files at the selected revision**, not just Karnataka. It does not load legislation/regulatory instruments into the judgment reader, and does not OCR PDF-only tribunals.

First inspect the plan (no downloads or model calls):

```bash
docker compose exec api python -m backend.bulk_import \
  --revision 58ea6d8b6859f8039ee68dff5af636f787b02463
```

To execute that plan:

```bash
docker compose exec -d api sh -c 'python -u -m backend.bulk_import --revision 58ea6d8b6859f8039ee68dff5af636f787b02463 --run > /data/bulk-import.log 2>&1'
docker compose exec api tail -f /data/bulk-import.log
```

The first command runs in the existing container independently of the Terminal tab. The log ends with explicit completion or an error. Ctrl+C on `tail -f` stops log viewing, not the importer. A container restart stops the importer: rerun the same command to resume. Run only one importer at a time. Logs are at `/data/bulk-import.log` in the persistent research volume. The command imports all rows, with no test cap, and saves checkpoints as it goes. It does **not** generate embeddings or call Gemini. Do not mistake a completed text import for complete semantic indexing.

Check progress from the app or with:

```bash
docker compose exec api python -m backend.ingest coverage
```

Select **All indexed courts** and **Keywords** in the interface to search the newly loaded text without query embedding calls. Keyword mode may still call Gemini to explain retrieved evidence. Meaning mode remains limited by the vectors that you separately build or restore, in addition to its keyword candidates.

The upstream documentation describes about 53.6 GB of compressed judgment Parquet, but source rows, reconstructed text, FTS, metadata, WAL and backups need additional disk. The CLI checks free space before each file, with 20 GB minimum reserve beyond that file's published size. This is a low-disk stop guard, **not** a reliable national index capacity estimate. Docker Desktop's virtual disk limit and the Mac's physical disk capacity both matter. A suitably sized server is preferable to a laptop for the whole corpus. The current SQLite pilot has not been benchmarked at national scale; completing the importer is not certification of production query performance.

New downloads use `HF_HOME=/data/huggingface`, so the Hugging Face cache is now persistent across API recreation. Existing source records and embeddings remain in the same Docker volumes. The importer uses a disk-backed document reconstruction queue, which avoids accumulating every document ID in memory or rebuilding the entire earlier corpus after every court file.

To select fewer files, add `--files in_supreme-court_judgments.parquet in_karnataka_judgments.parquet`. The default with no `--files` discovers all judgment files from the manifest. Completed files skip source rescanning on a repeated run. Using a different revision creates separate versioned documents; do not silently mix old/new releases without considering search deduplication.

## First test

```bash
docker compose exec api python -m backend.smoke_test
```

This checks the backend, prints real corpus/embedding counts, retrieves a random full stored document and validates the formatter. It makes no Gemini or embedding calls. It requires some imported records. The test uses the keys already in your existing `.env`; no need to paste them in Terminal.

## Use the interface

Open http://localhost:8000. Click **Connect**, leave Backend URL empty, and enter your BACKEND_API_TOKEN. Tokens and chat stay in tab memory, and clear on reload. Settings show actual local corpus and saved-embedding counts. Changing court/date scope clears the active judgment to prevent accidental cross-scope follow-ups.

Try:

- `Show a random full judgment` — returns the full available text directly, with no Gemini request.
- Ask a legal research question — searches the imported records and generates evidence-backed claims when possible.
- Open any judgment, then ask `Show the entire judgment` — returns that stored document, rather than asking a model to recreate it.
- Provide a CNR with a request for the entire judgment — matches within current court/date filters.

**Meaning** combines semantic and keyword candidates. **Keywords** bypasses embedding calls; generation can still call Gemini. **Exact phrase** searches reconstructed text. No mode searches every online case automatically. Relevant cases may be missing from your imported sample. If a request is slow, click **Stop**. Stopping the browser request does not guarantee that an already submitted provider request is cancelled or unbilled.

## Exactly what the formatter does

`backend/presentation.py` is deterministic. No AI call, spellchecker or external dictionary is used. It:

1. Normalises spaces, tabs and blank lines.
2. Inserts spaces at selected punctuation/paragraph boundaries and in a conservative list of common joined legal phrases (for example `writpetition` → `writ petition`).
3. Produces paragraph/heading blocks for the reader.

Every call compares the full input/output sequence **after removing whitespace**. If any remaining character differs, formatting fails. Letter case, names, numeric values, legal references, punctuation, original spelling errors, repeated text and uncertain OCR strings are retained. Unknown joined words are left alone rather than guessed. The reader represents known `[SECTION]`/`[TITLE]`/Markdown heading markers as styling; these markers remain in the downloadable text and raw source. This is a presentation pass, not a comprehensive repair of OCR word segmentation.

The raw `text` remains unchanged in the database and API. The API additionally returns `formatted_text`, `presentation_blocks` and a `formatting` object with identical input/output fingerprints. The reader has **Show raw text** and **Show readable text**, plus downloads for either version. Exact quote validation always uses raw source text. The formatter does not increase document completeness or validate case metadata.

## Repairs included

- Replaced the footer/reconnect code that accessed a deleted DOM node, causing `Cannot set properties of null`.
- Replaced the interface with a minimal chatbox and a functional native-dialog reader.
- Corrected Docker source-file permissions for the non-root app user.
- Added bounded provider request budgets, actionable quota/model/index errors and secret redaction in provider messages.
- Retry-After seconds are respected when within the request budget; long waits fail explicitly rather than hanging. Retries and bounded backoff cannot overcome a daily quota.
- Bulk embedding saves each successful chunk immediately. A later quota error does not discard that chunk. A configurable delay between document embeddings defaults to four seconds; this is not guaranteed sufficient for every account's request/token limits. Repeating the embedding command resumes from the persisted checkpoint.
- Checks the owned index before spending a query embedding call; empty corpora do not call embedding providers.
- Adds Keywords mode, which requires no embedding provider, and exposes existing saved-embedding counts in Settings.
- Full stored judgments can be returned directly through chat without any model call.
- Importer recognises `language_code` and retains optional original source metadata for newly ingested chunks. This does not repair incorrect upstream judge/case-number fields or supply missing citations/URLs.
- Text download uses the chosen readable/raw version; the optional Worker gateway forwards query parameters.
- Same-origin HTML is served with no-store to reduce stale-interface errors after updating.

## Embedding and testing research

The previous successful embeddings are retained. To cautiously add up to ten new chunks:

```bash
docker compose exec api python -m backend.ingest embed --limit 10
```

Provider calls may be billed. To change pacing, add `EMBEDDING_INTERVAL_SECONDS=6` (or a rate appropriate to your quota) to the existing `.env`, then recreate the API. Query requests do not use this bulk delay. On a quota error, inspect the provider's supplied message and your account limits. Do not regenerate a same-dimension index with a different embedding model; use a separate collection.

An optional real research smoke test (may call your configured Gemini API):

```bash
docker compose exec api python -m backend.smoke_test \
  --question 'interim injunctions in property disputes' --mode keyword
```

For Meaning mode, change `--mode keyword` to `--mode meaning`. Failure to find supporting material in a small sample is not proof that relevant case law does not exist.

## Corpus import

Existing imports remain usable. To discover source filenames and their revision:

```bash
docker compose exec api python -m backend.ingest list-hf
```

Your HF_TOKEN must belong to an account approved for the gated Vaquill dataset. List access does not necessarily permit download. Then import the selected file, using the revision actually printed:

```bash
docker compose exec api python -m backend.ingest hf \
  --file EXACT_FILENAME --revision PRINTED_SHA --max-rows 1000
```

The row limit limits imported chunks, not the download size. The entire selected Parquet is downloaded first, into the container's HF cache. Some judgments may be partial at a chunk limit. Omit the limit to resume and finish that selected file. Court overrides are optional and should only be used when the entire source file belongs to that court.

## Data quality and scope

The corpus and 32 previously generated embeddings were on your Mac and were not included in the uploaded source ZIP. Automated tests here use explicitly synthetic fixtures, temporary databases and mocked providers. No live accuracy evaluation or large-data performance certification is claimed.

All supplied chunks being present does not establish full original-PDF coverage. Missing citations and source links remain unavailable. Upstream judge lists, case numbers and OCR may contain errors. Raw source values are retained, never silently presented as verified. No citator/subsequent-treatment graph is included. Source quotation matching does not establish logical entailment or legal correctness. The current SQLite/FTS implementation is for a pilot; a nationwide corpus requires a production storage/search deployment and capacity evaluation.

The `.env` template is only for fresh installations. For a fresh setup, fill BACKEND_API_TOKEN, GEMINI_API_KEY, GEMINI_MODEL and HF_TOKEN. GEMINI_MODEL must be enabled for your Google project and support generateContent with structured JSON. The default owned embedding index uses gemini-embedding-001 at 768 dimensions. Published Vaquill snapshots instead require compatible Voyage embeddings and source mapping; that adapter remains in the code but was not exercised with live snapshots here.

## Developer checks

```bash
python -m pytest backend/tests -q
python tools/build-worker.py
node tools/test-worker.mjs
```

The source frontend is `frontend/index.html`. `tools/build-worker.py` embeds it in the optional Worker gateway. This repair targets the locally running Docker application; it does not automatically update a previously published Site. Hosted API keys belong in runtime secrets, not browser JavaScript. Browser-direct connections need HTTPS (except localhost) and a matching ALLOWED_ORIGINS setting.

Attribution: Open India Law · Vaquill AI · CC BY 4.0. Original court-source references are retained where available. https://github.com/Vaquill-AI/open-india-law and https://huggingface.co/datasets/vaquill/open-india-law.
