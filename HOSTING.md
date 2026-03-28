# HomeSignal — Hosting Analysis

## App Functionality Summary (from code analysis)

| Component | What It Does | Resource Profile |
|-----------|-------------|-----------------|
| **Streamlit Dashboard** (`frontend/app.py`) | Single-page UI with metro selector, 5 metric cards, Plotly trend chart, AI tooltips, daily market brief, RAG chat | ~150 MB RAM; serves HTTP on port 8501 |
| **RAG Engine** (`backend/rag.py`) | Retrieval-augmented generation — ChromaDB lookup + Claude API calls for chat, briefs, tooltips | ~80 MB RAM; needs Anthropic API access |
| **Embedding Model** | `all-MiniLM-L6-v2` loaded in-process by sentence-transformers | ~30 MB RAM; CPU-only (no GPU needed) |
| **ChromaDB** | Persistent vector store (~381 docs, 384-dim) in `data/chroma_db/` | ~50-100 MB disk; ~30 MB RAM index |
| **SQLite** (`data/homesignal.db`) | Stores redfin_metrics, fred_metrics, cached tooltips/briefs, feedback | ~2-5 MB disk |
| **Data Pipeline** (`pipeline/`) | 3 idempotent scripts: ingest FRED, ingest Redfin TSV, rebuild vectors | Peak ~650 MB RAM (Redfin parse); run manually or via cron |

### External API Dependencies

| API | When Called | Cost Impact |
|-----|-----------|-------------|
| **Anthropic (Claude Opus 4.6)** | Chat answers, daily briefs | ~$15/1M input tokens, ~$75/1M output tokens |
| **Anthropic (Claude Haiku)** | Hover tooltips (60 tokens each) | ~1/10th Opus cost; cached after first generation |
| **FRED API** | Pipeline only (`ingest_fred.py`) — 4 series calls | Free (API key required) |
| **Redfin** | No API — manual TSV.GZ download | Free |

### Runtime Requirements

- **RAM:** 250-400 MB steady state (app + embeddings + ChromaDB)
- **Disk:** ~1 GB (code + venv + data + ChromaDB)
- **CPU:** Light (no GPU needed; embeddings are small)
- **Network:** Outbound HTTPS to Anthropic API only
- **Ports:** 8501 (Streamlit default)
- **Concurrency:** Streamlit handles multiple users via session state; each user adds ~50 MB

---

## Hosting Options Comparison

### 1. Streamlit Community Cloud (Recommended for MVP / Demo)

**What:** Free hosting purpose-built for Streamlit apps.

**Setup:**
1. Push repo to GitHub (public or private)
2. Go to [share.streamlit.io](https://share.streamlit.io), connect repo
3. Set `ANTHROPIC_API_KEY` and `FRED_API_KEY` in Streamlit secrets
4. Deploy — it runs `streamlit run frontend/app.py` automatically

**Pros:**
- Zero cost, zero infrastructure
- Native Streamlit support (auto-detects `app.py`)
- Secrets management built in
- Auto-deploys on git push
- Custom subdomain (`homesignal.streamlit.app`)

**Cons:**
- **1 GB RAM limit** — tight with embedding model + ChromaDB; may need optimization
- Apps sleep after inactivity (cold start ~30s)
- No persistent disk — `data/` must be committed or rebuilt on boot
- No cron — pipeline scripts must run locally, data committed to repo
- Public apps only on free tier

**Best for:** Demos, portfolios, sharing with friends/recruiters.

**Estimated cost:** Free

---

### 2. Railway (Recommended for Production-Light)

**What:** PaaS with persistent volumes, easy deploy from GitHub.

**Setup:**
1. Add a `Procfile`: `web: streamlit run frontend/app.py --server.port $PORT --server.address 0.0.0.0`
2. Connect GitHub repo to Railway
3. Attach a persistent volume at `/app/data`
4. Set environment variables in Railway dashboard
5. Optionally add a cron service for pipeline runs

**Pros:**
- Persistent volumes (ChromaDB + SQLite survive deploys)
- 512 MB - 8 GB RAM (configurable)
- Cron jobs supported (run pipeline on schedule)
- Auto-deploy from GitHub
- Custom domains + free SSL
- Sleep on inactivity (cost savings)

**Cons:**
- Free tier: $5/month credit (enough for light use)
- Paid: usage-based (~$5-15/month for this app)

**Best for:** Personal project with real users; light production.

**Estimated cost:** $5-15/month

---

### 3. Render

**What:** PaaS similar to Railway, with a generous free tier.

**Setup:**
1. Create a `render.yaml` or configure via dashboard
2. Web service: `streamlit run frontend/app.py --server.port $PORT --server.address 0.0.0.0`
3. Attach a persistent disk for `data/`
4. Set env vars in dashboard
5. Use Render Cron Jobs for pipeline

**Pros:**
- Free tier for web services (750 hours/month)
- Persistent disks available ($0.25/GB/month)
- Native cron job support
- Auto-deploy from GitHub
- Custom domains + SSL

**Cons:**
- Free tier: 512 MB RAM (may be tight), spins down after 15 min inactivity
- Cold starts ~30-60s
- Persistent disk only on paid plan ($7/month starter)

**Best for:** Budget-conscious production deployment.

**Estimated cost:** $0-10/month

---

### 4. Fly.io

**What:** Container-based PaaS with global edge deployment.

**Setup:**
1. Create a `Dockerfile` (Python 3.11, install deps, copy app)
2. `fly launch` → auto-generates `fly.toml`
3. Attach a persistent volume: `fly volumes create homesignal_data --size 1`
4. Set secrets: `fly secrets set ANTHROPIC_API_KEY=... FRED_API_KEY=...`
5. Deploy: `fly deploy`

**Pros:**
- Persistent volumes (1 GB free)
- 256 MB - 8 GB RAM machines
- Scale to zero (pay nothing when idle)
- Global edge network (low latency)
- Custom domains + SSL
- Machines API for cron-like scheduling

**Cons:**
- Requires Docker knowledge
- Free tier: 3 shared-cpu-1x VMs + 1 GB volumes
- Networking more complex than Railway/Render

**Best for:** Cost-optimized production with scale-to-zero.

**Estimated cost:** $0-7/month

---

### 5. AWS EC2 / Lightsail

**What:** Traditional cloud VM.

**Setup:**
1. Launch Ubuntu instance (t3.small: 2 GB RAM recommended)
2. Install Python 3.11, clone repo, set up venv
3. Configure `.env` with API keys
4. Run pipeline scripts, then start Streamlit
5. Use `systemd` to keep Streamlit running
6. Set up Nginx reverse proxy + Let's Encrypt SSL
7. Add crontab entries for pipeline updates

**Pros:**
- Full control over environment
- Persistent storage by default
- Cron for pipeline scheduling
- No cold starts
- Can run multiple services

**Cons:**
- Manual server management (updates, SSL renewal, monitoring)
- Always-on cost (~$10-20/month for t3.small)
- Must configure firewall, reverse proxy, process management yourself

**Best for:** Full control, or when you already have AWS infrastructure.

| Instance | RAM | Cost |
|----------|-----|------|
| t3.micro | 1 GB | ~$8/month |
| t3.small | 2 GB | ~$15/month |
| Lightsail | 2 GB | $10/month (flat) |

---

### 6. Google Cloud Run (Serverless Container)

**What:** Serverless container platform — pay per request.

**Setup:**
1. Create `Dockerfile`
2. Build & push to Google Container Registry
3. Deploy to Cloud Run with env vars
4. Mount Cloud Storage for persistent data (or use Firestore)

**Pros:**
- Scale to zero (no cost when idle)
- Auto-scales for traffic spikes
- Managed SSL, custom domains
- No server management

**Cons:**
- **No persistent filesystem** — must use Cloud Storage or external DB for ChromaDB/SQLite
- Cold starts with embedding model loading (~10-15s)
- Architecture change needed: ChromaDB → external vector DB, SQLite → Cloud SQL
- More complex than PaaS options

**Best for:** High-scale production (but requires architecture changes).

**Estimated cost:** $0-5/month (low traffic), scales with usage

---

### 7. Docker on VPS (DigitalOcean / Hetzner / Linode)

**What:** Self-managed Docker on a cheap VPS.

**Setup:**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "frontend/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```
1. Provision VPS (2 GB RAM)
2. Install Docker, clone repo, build image
3. Use `docker-compose` with a data volume
4. Add Caddy/Nginx for reverse proxy + auto-SSL
5. Crontab for pipeline scripts

**Pros:**
- Cheapest always-on option ($4-6/month on Hetzner)
- Full control + persistent volumes
- Docker makes deployment reproducible
- Easy to add cron, monitoring

**Cons:**
- Manual server management
- Must handle backups, updates, SSL

**Best for:** Cost-sensitive always-on deployment.

| Provider | 2 GB RAM | Cost |
|----------|----------|------|
| Hetzner | CX22 | ~$4/month |
| DigitalOcean | Basic | ~$12/month |
| Linode | Nanode 2GB | ~$12/month |

---

## Recommendation Matrix

| Priority | Best Option | Why |
|----------|------------|-----|
| **Free demo/portfolio** | Streamlit Community Cloud | Zero cost, zero config, purpose-built |
| **Low-cost production** | Railway or Fly.io | Persistent storage, cron support, $5-15/month |
| **Budget always-on** | Hetzner VPS + Docker | $4/month, full control, persistent data |
| **Scalable production** | Cloud Run + managed DBs | Auto-scales, but needs architecture changes |
| **Full control** | AWS EC2 / Lightsail | Traditional VM, $10-20/month |

## Pre-Deployment Checklist

- [ ] Ensure `.env` is in `.gitignore` (never commit API keys)
- [ ] Run full pipeline: `ingest_fred.py` → `ingest_redfin.py` → `update_vectors.py`
- [ ] Verify ChromaDB and SQLite files exist in `data/`
- [ ] Test locally: `streamlit run frontend/app.py`
- [ ] For platforms without persistent disk: commit `data/homesignal.db` and `data/chroma_db/` to repo
- [ ] Set `ANTHROPIC_API_KEY` and `FRED_API_KEY` as platform secrets/env vars
- [ ] For production: set up scheduled pipeline runs (cron/scheduler)
- [ ] For production: add a reverse proxy (Nginx/Caddy) with SSL
- [ ] Monitor Anthropic API usage to control costs (Opus calls are expensive)
