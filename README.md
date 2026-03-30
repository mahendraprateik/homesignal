# HomeSignal

AI-powered housing market intelligence. Select a metro area and get real-time metrics, price trends, daily AI market briefs, and answers to your questions via a RAG-powered chat.

## How It Works

HomeSignal pulls housing data from **Redfin** and economic indicators from **FRED**, stores everything in SQLite + ChromaDB, and uses **Claude** to generate insights and answer questions. A centralized **semantic model** (`data/semantic_model.yaml`) drives metric definitions across the entire stack — pipeline, RAG, chat, and frontend.

### Data Flow

```
Redfin (TSV) ──┐                        ┌── ChromaDB (vectors)
                ├── data_ingestion.py ──►├── SQLite (structured data)
FRED API ───────┘                        └── Ready for queries
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
           (Redfin vectors)        (all structured data)
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
  Metro Detection (alias map)
      │
      v
  ┌───┴────────────────────┐
  │  ChromaDB Retrieval    │──► Redfin market docs (top-5 per metro)
  │  SQL Tool-Use          │──► FRED data (mortgage, CPI, unemployment, housing starts)
  └───┬────────────────────┘
      │
      v
  Claude (claude-opus-4-6)
      │
      v
  Cited Answer with [1][2] markers
```

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | Streamlit |
| LLM | Claude (Opus for answers, Haiku for tooltips) |
| Embeddings | `BAAI/bge-small-en-v1.5` (local, sentence-transformers) |
| Vector DB | ChromaDB (persistent, `data/chroma_db/`) |
| Relational DB | SQLite (`data/homesignal.db`) |
| Charts | Plotly |
| Data Sources | Redfin (TSV), FRED API |
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

# 3. Run the data pipeline
python pipeline/data_ingestion.py    # download + ingest all data
python pipeline/update_vectors.py    # build vector store

# 4. Launch the app
streamlit run home_signal_frontend/app.py
# Opens at http://localhost:8501
```

## Project Structure

```
homesignal/
├── home_signal_frontend/
│   ├── app.py                  # Streamlit UI (imports only backend.api + formatting)
│   └── formatting.py           # Pure display helpers (no DB/backend deps)
├── backend/
│   ├── api.py                  # Single entry point for all frontend calls
│   ├── rag.py                  # RAGEngine — retrieval + Claude inference
│   ├── chat_engine.py          # Hybrid RAG + SQL tool-use engine
│   ├── cloud_sync.py           # Pull latest data snapshot from GCS
│   ├── formatting_utils.py     # Shared formatting helpers (used by api + frontend)
│   └── semantic_model.py       # Loads data/semantic_model.yaml for typed metric access
├── pipeline/
│   ├── data_ingestion.py       # Download, clean, load all data → SQLite
│   ├── refresh.py              # Freshness checks + orchestrates ingestion + vector rebuild
│   ├── update_vectors.py       # SQLite → ChromaDB vector store
│   ├── cloud_refresh_job.py    # Entrypoint for scheduled Cloud Build refresh
│   ├── run_all.py              # Run full pipeline end-to-end
│   └── context_ingestion/      # Context document ingestion sub-pipeline
├── data/
│   ├── homesignal.db           # SQLite database
│   ├── chroma_db/              # ChromaDB persistent vectors
│   ├── semantic_model.yaml     # Metric definitions (single source of truth)
│   └── raw/                    # Redfin TSV.GZ source files
├── evals/
│   ├── golden_dataset.json     # Eval test cases
│   ├── run_evals.py            # RAG evaluation runner
│   └── smoke_connectivity.py   # Connectivity smoke tests
├── tests/                      # Unit tests
├── Dockerfile                  # Container image for Cloud Run
├── cloudbuild.refresh.yaml     # Cloud Build config for scheduled refresh
├── .streamlit/config.toml      # Streamlit theme (teal/off-white)
├── .env                        # API keys (never committed)
└── .env.gcp.yaml               # Env vars for Cloud Run deploys
```

## GCP Deployment (Cloud Run)

### Current cloud resources

- GCP Project: `homesignal-491722`
- Cloud Run service: `homesignal-asis`
- Region: `us-central1`
- Artifact Registry repo: `cloud-run-source-deploy`
- Runtime data bucket: `gs://homesignal-491722-homesignal-data`
- Snapshot manifest: `gs://homesignal-491722-homesignal-data/homesignal/latest.json`
- Snapshot archives: `gs://homesignal-491722-homesignal-data/homesignal/snapshots/`

### Deploy app

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

## Scheduled Data Refresh (3:00 AM PST)

### Architecture

1. Cloud Scheduler publishes a daily message to Pub/Sub.
2. A Cloud Build trigger listens to that topic.
3. Cloud Build runs `pipeline/cloud_refresh_job.py --skip-context` inside the app image.
4. Refresh builds a snapshot and uploads `latest.json` + archive to GCS.
5. App runtime checks manifest periodically via `backend/cloud_sync.py` and syncs newer snapshots silently.

### Cloud Build Trigger

- Trigger name: `homesignal-refresh-daily`
- Topic: `projects/homesignal-491722/topics/homesignal-refresh-topic`
- Build config: `cloudbuild.refresh.yaml`
- Refresh command: `cd /app && python pipeline/cloud_refresh_job.py --skip-context`

```bash
PROJECT_ID=homesignal-491722
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
SA_RESOURCE="projects/${PROJECT_ID}/serviceAccounts/${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
TOPIC_RESOURCE="projects/${PROJECT_ID}/topics/homesignal-refresh-topic"

gcloud builds triggers create pubsub \
  --name homesignal-refresh-daily \
  --description "Daily HomeSignal refresh via PubSub trigger" \
  --region global \
  --service-account "${SA_RESOURCE}" \
  --topic "${TOPIC_RESOURCE}" \
  --subscription-filter "true" \
  --inline-config cloudbuild.refresh.yaml
```

### Cloud Scheduler

- Job: `homesignal-refresh-3am`
- Schedule: `0 3 * * *` (America/Los_Angeles)
- Target: Pub/Sub topic `projects/homesignal-491722/topics/homesignal-refresh-topic`

```bash
PROJECT_ID=homesignal-491722
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
TOPIC_RESOURCE="projects/${PROJECT_ID}/topics/homesignal-refresh-topic"
SCHED_AGENT="service-${PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com"

gcloud pubsub topics add-iam-policy-binding "${TOPIC_RESOURCE}" \
  --member "serviceAccount:${SCHED_AGENT}" \
  --role roles/pubsub.publisher

gcloud scheduler jobs create pubsub homesignal-refresh-3am \
  --location us-central1 \
  --schedule "0 3 * * *" \
  --time-zone "America/Los_Angeles" \
  --topic "${TOPIC_RESOURCE}" \
  --message-body '{"trigger":"daily-refresh"}'
```

### IAM required

Cloud Build service account needs bucket write + image pull access:

```bash
PROJECT_ID=homesignal-491722
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
CLOUDBUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

gcloud storage buckets add-iam-policy-binding gs://homesignal-491722-homesignal-data \
  --member "serviceAccount:${CLOUDBUILD_SA}" \
  --role roles/storage.objectAdmin

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${CLOUDBUILD_SA}" \
  --role roles/artifactregistry.reader
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

## Notes

- Cloud Run local filesystem is ephemeral. Durable data refresh depends on GCS snapshot publish + app sync.
- `.env.gcp.yaml` contains plain text keys for testing. Move to Secret Manager for production.
- Context ingestion (`pipeline/context_ingestion/`) is currently skipped in scheduled refresh to reduce runtime and memory pressure.
- `google-cloud-storage` is only imported lazily — the app runs locally without GCP credentials.
