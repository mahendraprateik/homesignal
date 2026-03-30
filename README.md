# HomeSignal

AI-powered housing market intelligence. Select a metro area and get real-time metrics, price trends, daily AI market briefs, and answers to your questions via a RAG-powered chat.

## How It Works

HomeSignal pulls housing data from **Redfin** and economic indicators from **FRED**, stores everything in SQLite + ChromaDB, and uses **Claude** to generate insights and answer questions.

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
| Embeddings | `BAAI/bge-small-en-v1.5` (local) |
| Vector DB | ChromaDB |
| Relational DB | SQLite |
| Charts | Plotly |
| Data Sources | Redfin, FRED API |

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
├── home_signal_frontend/    # Streamlit UI + display helpers
├── backend/                 # API layer, RAG engine, chat engine
├── pipeline/                # Data ingestion, refresh, vector building
├── data/                    # SQLite DB, ChromaDB vectors, raw files
└── .env                     # API keys (not committed)
```

## GCP Deployment (Cloud Run)

This repo is currently set up to run on Cloud Run with local runtime artifacts:

- `data/homesignal.db` (SQLite)
- `data/chroma_db/` (Chroma vectors)

### Current cloud resources

- GCP Project: `homesignal-491722`
- Cloud Run service: `homesignal-asis`
- Region: `us-central1`
- Artifact Registry repo: `cloud-run-source-deploy`
- Runtime data bucket: `gs://homesignal-491722-homesignal-data`
- Snapshot manifest: `gs://homesignal-491722-homesignal-data/homesignal/latest.json`
- Snapshot archives: `gs://homesignal-491722-homesignal-data/homesignal/snapshots/`

### Deployment files used in this repo

- `Dockerfile` - container image for Streamlit app
- `.gcloudignore` - excludes heavy/local-only paths from source deploys
- `.env.gcp.yaml` - env var file used for Cloud Run deploys (currently plain text keys)

### Deploy app (as currently configured)

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

### Goal

Run refresh in the background daily without user interaction in the app UI.

### Architecture

1. Cloud Scheduler publishes a daily message to Pub/Sub.
2. A Cloud Build trigger listens to that topic.
3. Cloud Build runs `pipeline/cloud_refresh_job.py --skip-context` inside the app image.
4. Refresh builds a snapshot and uploads `latest.json` + archive to GCS.
5. App runtime checks manifest periodically and syncs newer snapshot silently.
6. UI does not expose manual refresh button.

### Code changes for this flow

- Added `pipeline/cloud_refresh_job.py` (job entrypoint)
- Added `backend/cloud_sync.py` (pull latest snapshot from GCS)
- Added `api.maybe_sync_cloud_data()` in `backend/api.py`
- Added `cached_maybe_sync_cloud_data()` in `home_signal_frontend/app.py`
- Added `google-cloud-storage` dependency
- Removed UI refresh controls from sidebar

### Cloud Build Trigger config

- Trigger name: `homesignal-refresh-daily`
- Trigger type: Pub/Sub
- Topic: `projects/homesignal-491722/topics/homesignal-refresh-topic`
- Build config: `cloudbuild.refresh.yaml`
- Runner image: `us-central1-docker.pkg.dev/homesignal-491722/cloud-run-source-deploy/homesignal-asis:latest`
- Refresh command in build step: `cd /app && python pipeline/cloud_refresh_job.py --skip-context`

Create/update trigger:

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

### Cloud Scheduler cron config

- Scheduler job: `homesignal-refresh-3am`
- Schedule: `0 3 * * *`
- Time zone: `America/Los_Angeles`
- Target: Pub/Sub topic `projects/homesignal-491722/topics/homesignal-refresh-topic`
- Message body: `{"trigger":"daily-refresh"}`

Create/update scheduler:

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

- Cloud Build service account needs bucket write access + image pull access:

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

### Check service logs

```bash
gcloud run services logs read homesignal-asis --region us-central1 --limit 200
```

### Check refresh build logs

```bash
gcloud logging read \
  'resource.type="build" AND protoPayload.resourceName:"triggers/homesignal-refresh-daily"' \
  --limit=200
```

### Trigger refresh manually

```bash
gcloud scheduler jobs run homesignal-refresh-3am --location us-central1
```

### Check latest build status

```bash
gcloud builds list --limit=10
gcloud builds describe BUILD_ID
```

## Notes

- Cloud Run local filesystem is ephemeral. Durable data refresh depends on GCS snapshot publish + app sync.
- Current `.env.gcp.yaml` contains plain text keys for testing. Move to Secret Manager for production.
- Context refresh (`pipeline/context_ingestion`) is currently skipped in scheduled refresh to reduce runtime and memory pressure.
