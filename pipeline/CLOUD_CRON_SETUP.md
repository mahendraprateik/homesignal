# HomeSignal Cloud Refresh (3:00 AM PST)

This setup runs refresh via a Cloud Build trigger fired by Cloud Scheduler
(through Pub/Sub), then publishes a snapshot to GCS. The app pulls the newest
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

## 4) Create Cloud Build trigger (Pub/Sub event)

```bash
gcloud services enable cloudbuild.googleapis.com pubsub.googleapis.com

gcloud pubsub topics create homesignal-refresh-topic

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

## 5) Allow Cloud Build identity to read image + write snapshots

```bash
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
CLOUDBUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member "serviceAccount:${CLOUDBUILD_SA}" \
  --role roles/storage.objectAdmin

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${CLOUDBUILD_SA}" \
  --role roles/artifactregistry.reader
```

## 6) Create daily scheduler at 3:00 AM PST (Pub/Sub target)

```bash
gcloud services enable cloudscheduler.googleapis.com

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

## 7) Manual test

```bash
gcloud scheduler jobs run homesignal-refresh-3am --location us-central1
gcloud builds list --limit=10
gcloud builds describe BUILD_ID
```

