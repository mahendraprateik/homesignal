# HomeSignal

AI-powered housing market intelligence. Select a metro area and get real-time metrics, price trends, daily AI market briefs, and answers to your questions via a RAG-powered chat.

## How It Works

HomeSignal pulls housing data from **Redfin** (7 geography levels), economic indicators from **FRED**, and market context from **web sources** (Redfin News, Zillow Research, Federal Reserve, BLS, Census, Freddie Mac). Everything is stored in SQLite + ChromaDB and powered by **Claude** for insights and Q&A. A centralized **semantic model** (`data/semantic_model.yaml`) drives metric definitions across the entire stack — pipeline, RAG, chat, and frontend.

### Data Flow

```
Redfin (TSV, 7 geo levels) ──┐                        ┌── ChromaDB `housing_market` (Redfin vectors + trend summaries)
                              ├── data_ingestion.py ──►├── SQLite (structured data)
FRED API (4 series) ──────────┘                        └── Ready for queries

Web Sources (7 sites) ──► context_ingestion/ ──► ChromaDB `housing_context` (enriched article chunks)
```

### User Request Flow

```
                    ┌─────────────────────────────┐
                    │     Streamlit Frontend       │
                    │  metrics | chart | chat | AI │
                    └─────────────┬───────────────┘
                                  │
                          backend/api.py
                       (single entry point)
                          /           \
                   backend/rag.py   backend/chat_engine.py
                   (retrieval +     (hybrid RAG + SQL
                    inference)       tool-use engine)
                      /                    \
              ChromaDB                   SQLite
         (Redfin vectors +          (all structured data)
          web context docs)
                      \                    /
                       └──── Claude LLM ──┘
                         (generates answers,
                          briefs, tooltips)
```

### Chat Architecture

```
User Question
      │
      v
  Metro Detection (alias map — supports "Phoenix", "Phoenix, AZ", full names)
      │
      v
  ┌───┴────────────────────┐
  │  ChromaDB Retrieval    │──► Redfin market docs (top-5 per metro)
  │                        │──► Web context docs (market narratives, forecasts)
  │  SQL Tool-Use          │──► query_latest_metrics(metro)
  │                        │──► query_mortgage_rate()
  │                        │──► compare_metros(metro_names[])
  │                        │──► top_metros_by_metric(metric, order, limit)
  └───┬────────────────────┘
      │  (up to 3 tool-use iterations)
      v
  Claude (claude-opus-4-6) with conversational memory (last 6 turns)
      │
      v
  Cited Answer with [1][2] markers + confidence score
```

## Data Sources

### Redfin Housing Data (7 Geography Levels)

Downloaded from Redfin's public S3 bucket. Filtered to "All Residential", non-seasonally-adjusted, last 18 months.

| Geography | Top-N Filter | Example |
|-----------|-------------|---------|
| Metro | 20 | "Phoenix, AZ metro area" |
| City | 100 | "San Jose, CA" |
| County | 100 | "King County, WA" |
| State | All | "California" |
| Neighborhood | 100 | "Capitol Hill, Seattle" |
| ZIP Code | 200 | "98101" |
| National | All | US overall |

**12 metrics ingested** (defined in `data/semantic_model.yaml`): median sale price, days on market, inventory, price drop %, homes sold, new listings, months of supply, avg sale-to-list ratio, sold above list %, price MoM, price YoY, inventory MoM.

### FRED Economic Indicators (4 Series)

Fetched via FRED API. Stored in SQLite only (not embedded in vectors). Accessed by ChatEngine's SQL tools at query time.

| Series | Description | Cadence |
|--------|-------------|---------|
| MORTGAGE30US | 30-year fixed mortgage rate | Weekly |
| CPIAUCSL | Consumer Price Index | Monthly |
| UNRATE | Unemployment rate | Monthly |
| HOUST | Housing starts (thousands) | Monthly |

### Web Context Documents (7 Sources)

Scraped by `pipeline/context_ingestion/`, chunked, enriched with metadata, and embedded into a separate ChromaDB collection (`housing_context`).

| Source | Content |
|--------|---------|
| Redfin News | Market narratives on pricing, inventory, demand |
| Zillow Research | Pricing trends, demand analysis, forecasts |
| Freddie Mac PMMS | Mortgage rate context |
| Federal Reserve | Interest rates, inflation, monetary policy |
| BLS | Employment and inflation data |
| US Census Bureau | Housing supply, construction statistics |
| Redfin Migration | Migration trends and patterns |

**Enrichment pipeline**: Each chunk is tagged with detected signals (prices_up/down, inventory_up/down, etc.), drivers (mortgage_rates, inflation, employment, etc.), metrics mentioned, and inferred topic. Chunks are deduplicated by cosine similarity before insertion.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | Streamlit |
| LLM | Claude (Opus for answers/briefs, Haiku for tooltips/evals) |
| Embeddings | `BAAI/bge-small-en-v1.5` (local, sentence-transformers) |
| Vector DB | ChromaDB — `housing_market` (Redfin) + `housing_context` (web docs) |
| Relational DB | SQLite (`data/homesignal.db`) |
| Charts | Plotly |
| Semantic Model | YAML (`data/semantic_model.yaml`) — single source of truth for all metric definitions |
| Cloud | GCP Cloud Run, Cloud Build, Cloud Scheduler, GCS |

## Quickstart

```bash
# 1. Set up environment
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Configure API keys in .env
ANTHROPIC_API_KEY=your-key
FRED_API_KEY=your-key

# 3. Run the full data pipeline
python pipeline/run_all.py            # download + ingest + vectors + context

# 4. Launch the app
streamlit run home_signal_frontend/app.py
# Opens at http://localhost:8501
```

## Pipeline

All scripts are idempotent and can be run independently.

### Full Pipeline (`pipeline/run_all.py`)

Orchestrates everything with smart freshness checks (FRED stale > 8 days, Redfin stale > 35 days).

```bash
python pipeline/run_all.py                # smart refresh — only runs stale steps
python pipeline/run_all.py --force        # force full rebuild
python pipeline/run_all.py --skip-context # skip web context ingestion (faster)
python pipeline/run_all.py --context-only # only run context ingestion
```

### Individual Steps

```bash
# Data ingestion (Redfin + FRED → SQLite)
python pipeline/data_ingestion.py              # both sources
python pipeline/data_ingestion.py --redfin     # Redfin only (downloads + ingests)
python pipeline/data_ingestion.py --fred       # FRED only
python pipeline/data_ingestion.py --download   # download Redfin files only
python pipeline/data_ingestion.py --redfin --no-download  # ingest existing files

# Vector store (SQLite → ChromaDB)
python pipeline/update_vectors.py              # rebuilds housing_market collection

# Freshness check + conditional refresh
python pipeline/refresh.py                     # only refreshes stale data
python pipeline/refresh.py --force             # force refresh
python pipeline/refresh.py --check-only        # report staleness without acting
```

### Vector Store Contents

`pipeline/update_vectors.py` generates three document types in the `housing_market` collection:

1. **market_data** — One doc per (metro, state, period) with all formatted metrics
2. **metro_trend** — 18-month trend summary per metro (price ranges, direction, YoY/MoM)
3. **metric_definition** — Single grounding doc with all metric definitions (prevents hallucination)

The `housing_context` collection is populated separately by the context ingestion sub-pipeline.

## Evaluation System

### LLM-as-Judge (`evals/run_evals.py`)

Runs questions from `evals/golden_dataset.json` through ChatEngine and scores answers using Claude Haiku.

```bash
python evals/run_evals.py                      # run full eval suite
```

**Three eval types**:
- **metric_accuracy** (40% weight) — Are expected facts and numbers present and correct?
- **reasoning** (40% weight) — Does the answer reference expected concepts with sound logic?
- **guardrail** (20% weight) — Does the system properly decline out-of-scope questions (predictions, valuations)?

Results broken down by difficulty (easy/medium/hard) are written to `evals/eval_results.json`.

### Smoke Tests (`evals/smoke_connectivity.py`)

Fast, non-LLM validation of core wiring: pipeline functions, backend API reads, chat signatures, and ChromaDB collection health.

```bash
python evals/smoke_connectivity.py             # basic checks
python evals/smoke_connectivity.py --deep      # includes live semantic retrieval
```

## Frontend Features

### Metric Cards
5 dashboard cards (configured via semantic model): Median Sale Price, Days on Market, Inventory, Price Drop %, 30yr Mortgage Rate. Each shows current value, delta with color-coded arrows, and AI-generated hover tooltips (Haiku, cached in SQLite).

### Trend Chart
Plotly line chart with 12-month historical data. Teal styling with hover tooltips.

### Daily AI Brief
Claude Opus generates a market summary once per metro per day. Cached in SQLite `ai_briefs` table.

### Chat
Multi-turn conversation (up to 50 messages per metro). Uses ChatEngine with RAG retrieval + SQL tool-use. Displays confidence score and cited sources. Thumbs up/down feedback logged to SQLite `feedback` table.

## Project Structure

```
homesignal/
├── home_signal_frontend/
│   ├── app.py                  # Streamlit UI (imports only backend.api + formatting)
│   └── formatting.py           # Pure display helpers (no DB/backend deps)
├── backend/
│   ├── api.py                  # Single entry point for all frontend calls
│   ├── rag.py                  # RAGEngine — retrieval + Claude inference
│   ├── chat_engine.py          # Hybrid RAG + SQL tool-use engine (4 SQL tools)
│   ├── cloud_sync.py           # Pull latest data snapshot from GCS at runtime
│   ├── formatting_utils.py     # Shared formatting helpers
│   └── semantic_model.py       # Loads data/semantic_model.yaml for typed metric access
├── pipeline/
│   ├── run_all.py              # Full pipeline orchestrator (freshness-aware)
│   ├── data_ingestion.py       # Download, clean, load Redfin + FRED → SQLite
│   ├── update_vectors.py       # SQLite → ChromaDB housing_market collection
│   ├── refresh.py              # Freshness checks + conditional ingestion
│   ├── cloud_refresh_job.py    # Cloud Build entrypoint (pipeline + GCS snapshot)
│   └── context_ingestion/      # Web context sub-pipeline
│       ├── config.py           # Source URLs and scraping config
│       ├── discovery.py        # Crawl base URLs, find article links
│       ├── extraction.py       # HTML → clean text
│       ├── chunking.py         # Semantic chunking (80-250 words)
│       ├── enrichment.py       # Signal/driver/metric/topic tagging
│       ├── summarization.py    # Chunk summaries + key points
│       ├── vectorstore.py      # Embed + deduplicate + insert to ChromaDB
│       └── pipeline.py         # Orchestrates all context ingestion steps
├── data/
│   ├── homesignal.db           # SQLite database
│   ├── chroma_db/              # ChromaDB persistent vectors (2 collections)
│   ├── semantic_model.yaml     # Metric definitions (single source of truth)
│   └── raw/                    # Redfin TSV.GZ source files (~5 GB)
├── evals/
│   ├── golden_dataset.json     # Eval test cases (metric_accuracy, reasoning, guardrail)
│   ├── run_evals.py            # LLM-as-judge evaluation runner
│   ├── eval_results.json       # Latest eval scores
│   └── smoke_connectivity.py   # Fast non-LLM wiring checks
├── tests/                      # Unit tests
├── Dockerfile                  # Container image for Cloud Run
├── cloudbuild.refresh.yaml     # Cloud Build config for scheduled refresh
├── .streamlit/config.toml      # Streamlit theme (teal/off-white)
├── .env                        # API keys (never committed)
└── .env.gcp.yaml               # Env vars for Cloud Run deploys
```

## SQLite Schema

| Table | Purpose |
|-------|---------|
| `redfin_metrics` | Housing metrics across 7 geography levels; `region_type` column distinguishes them |
| `fred_metrics` | FRED economic series: MORTGAGE30US, CPIAUCSL, UNRATE, HOUST |
| `ai_tooltips` | Cached AI hover tooltips keyed by (metro, period, metric) |
| `ai_briefs` | Cached daily market briefs keyed by (metro, date) |
| `feedback` | Thumbs up/down on chat answers |

## GCP Deployment (Cloud Run)

### Cloud Resources

| Resource | Value |
|----------|-------|
| GCP Project | `homesignal-491722` |
| Cloud Run Service | `homesignal-asis` |
| Region | `us-central1` |
| Artifact Registry | `cloud-run-source-deploy` |
| GCS Bucket | `gs://homesignal-491722-homesignal-data` |
| Snapshot Manifest | `homesignal/latest.json` |

### Deploy App

```bash
gcloud run deploy homesignal-asis \
  --image us-central1-docker.pkg.dev/homesignal-491722/cloud-run-source-deploy/homesignal-asis:latest \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 1 \
  --timeout 300 \
  --max-instances 1 \
  --env-vars-file .env.gcp.yaml \
  --quiet
```

### Scheduled Data Refresh (3:00 AM PST)

**Architecture**: Cloud Scheduler -> Pub/Sub -> Cloud Build -> GCS snapshot -> App runtime sync

1. **Cloud Scheduler** (`homesignal-refresh-3am`) publishes daily to Pub/Sub at `0 3 * * *` PST.
2. **Cloud Build** trigger (`homesignal-refresh-daily`) runs `pipeline/cloud_refresh_job.py --skip-context`.
3. **cloud_refresh_job.py** runs the full pipeline, creates a tar.gz snapshot of `homesignal.db` + `chroma_db/`, uploads to GCS with a `latest.json` manifest (includes SHA256, size, timestamp).
4. **App runtime** (`backend/cloud_sync.py`) periodically checks the manifest and atomically applies newer snapshots.

Context ingestion is skipped in scheduled refresh (`--skip-context`) to reduce runtime and memory.

### Setup Commands

```bash
PROJECT_ID=homesignal-491722
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

# Cloud Build trigger
gcloud builds triggers create pubsub \
  --name homesignal-refresh-daily \
  --region global \
  --service-account "projects/${PROJECT_ID}/serviceAccounts/${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --topic "projects/${PROJECT_ID}/topics/homesignal-refresh-topic" \
  --subscription-filter "true" \
  --inline-config cloudbuild.refresh.yaml

# Cloud Scheduler
gcloud scheduler jobs create pubsub homesignal-refresh-3am \
  --location us-central1 \
  --schedule "0 3 * * *" \
  --time-zone "America/Los_Angeles" \
  --topic "projects/${PROJECT_ID}/topics/homesignal-refresh-topic" \
  --message-body '{"trigger":"daily-refresh"}'

# IAM — Cloud Build needs bucket write + image pull
CLOUDBUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
gcloud storage buckets add-iam-policy-binding gs://homesignal-491722-homesignal-data \
  --member "serviceAccount:${CLOUDBUILD_SA}" --role roles/storage.objectAdmin
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${CLOUDBUILD_SA}" --role roles/artifactregistry.reader
```

## Operations

```bash
# Service logs
gcloud run services logs read homesignal-asis --region us-central1 --limit 200

# Refresh build logs
gcloud logging read \
  'resource.type="build" AND protoPayload.resourceName:"triggers/homesignal-refresh-daily"' \
  --limit=200

# Trigger refresh manually
gcloud scheduler jobs run homesignal-refresh-3am --location us-central1

# Check recent builds
gcloud builds list --limit=10
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `FRED_API_KEY` | Yes | FRED API key |
| `HOMESIGNAL_GCS_BUCKET` | No | GCS bucket for cloud snapshot sync |
| `HOMESIGNAL_GCS_PREFIX` | No | Path prefix in bucket (default: `homesignal`) |

## Notes

- Cloud Run filesystem is ephemeral. Durable data depends on GCS snapshot publish + app sync.
- `.env.gcp.yaml` contains plain text keys for testing. Move to Secret Manager for production.
- `google-cloud-storage` is only imported lazily — the app runs locally without GCP credentials.
- FRED data is NOT embedded in vectors — it is accessed via ChatEngine SQL tools at query time.
- Feedback is logged to SQLite but not currently used to influence answer quality.
