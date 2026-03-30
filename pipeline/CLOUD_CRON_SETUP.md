# HomeSignal Cloud Refresh (3:00 AM PST)

This setup runs data/context refresh in a separate Cloud Run Job on a daily
schedule, then publishes a snapshot to GCS. The web app can pull the latest
snapshot in the background (no in-UI refresh button).

## 1) Required env vars

- `HOMESIGNAL_GCS_BUCKET` (required)
- `HOMESIGNAL_GCS_PREFIX` (optional, default: `homesignal`)
- `ANTHROPIC_API_KEY`
- `FRED_API_KEY`

## 2) Create a storage bucket

```bash
PROJECT_ID=homesignal-491722
REGION=us-central1
BUCKET="${PROJECT_ID}-homesignal-data"

gcloud storage buckets create "gs://${BUCKET}" \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --uniform-bucket-level-access
```

## 3) Deploy the app with cloud-sync enabled

```bash
gcloud run deploy homesignal-asis \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 1 \
  --timeout 300 \
  --max-instances 1 \
  --env-vars-file .env.gcp.yaml \
  --set-env-vars HOMESIGNAL_GCS_BUCKET="${BUCKET}",HOMESIGNAL_GCS_PREFIX=homesignal \
  --quiet
```

## 4) Create refresh job (separate from web service)

```bash
gcloud run jobs deploy homesignal-refresh \
  --image us-central1-docker.pkg.dev/homesignal-491722/cloud-run-source-deploy/homesignal-asis:latest \
  --region us-central1 \
  --memory 2Gi \
  --cpu 1 \
  --max-retries 1 \
  --task-timeout 3600 \
  --set-env-vars ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY}",FRED_API_KEY="${FRED_API_KEY}",HOMESIGNAL_GCS_BUCKET="${BUCKET}",HOMESIGNAL_GCS_PREFIX=homesignal \
  --command python \
  --args pipeline/cloud_refresh_job.py \
  --quiet
```

## 5) Allow job identity to write to bucket

```bash
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member "serviceAccount:${SA}" \
  --role roles/storage.objectAdmin
```

## 6) Create daily scheduler at 3:00 AM PST

```bash
gcloud services enable cloudscheduler.googleapis.com

gcloud scheduler jobs create http homesignal-refresh-3am \
  --location us-central1 \
  --schedule "0 3 * * *" \
  --time-zone "America/Los_Angeles" \
  --uri "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/homesignal-refresh:run" \
  --http-method POST \
  --oauth-service-account-email "${SA}" \
  --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform"
```

## 7) Manual test

```bash
gcloud run jobs execute homesignal-refresh --region us-central1 --wait
gcloud run jobs executions list --job homesignal-refresh --region us-central1
```

