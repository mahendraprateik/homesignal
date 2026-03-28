# HomeSignal — CLAUDE.md

## Project Overview

HomeSignal is an AI-powered housing market intelligence app. Users select a metro area and see real-time metrics, a 12-month price trend chart, a daily AI market brief, and can ask questions via a RAG-powered chat interface.

## Architecture

```
homesignal/
├── frontend/app.py          # Streamlit single-page dashboard
├── backend/rag.py           # RAGEngine — retrieval + Claude inference
├── pipeline/
│   ├── ingest_fred.py       # FRED API → SQLite (mortgage rates)
│   ├── ingest_redfin.py     # Redfin TSV.GZ → SQLite (metro metrics)
│   └── update_vectors.py    # SQLite → ChromaDB vector store
├── data/
│   ├── homesignal.db        # SQLite: all tables
│   ├── chroma_db/           # ChromaDB persistent vectors
│   └── raw/                 # Redfin TSV.GZ source file
├── .streamlit/config.toml   # Streamlit theme (teal/off-white)
├── .env                     # ANTHROPIC_API_KEY, FRED_API_KEY (never commit)
└── verify_keys.py           # Test API key validity
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Web UI | Streamlit |
| LLM | Claude Opus 4.6 (`claude-opus-4-6`), Haiku for tooltips |
| Embeddings | `all-MiniLM-L6-v2` (local, sentence-transformers) |
| Vector DB | ChromaDB (persistent, `data/chroma_db/`) |
| Relational DB | SQLite (`data/homesignal.db`) |
| Charts | Plotly |
| Data Sources | FRED API (mortgage rates), Redfin TSV (metro metrics) |
| Package Manager | pip + venv |

## Environment & Run

```bash
source venv/bin/activate
python verify_keys.py                          # confirm API keys
./venv/bin/streamlit run frontend/app.py       # http://localhost:8501
```

## Data Pipeline (run in order)

```bash
python pipeline/ingest_fred.py      # fetch mortgage rates
python pipeline/ingest_redfin.py    # ingest Redfin snapshot
python pipeline/update_vectors.py   # rebuild ChromaDB (clears + recreates)
```

All three scripts are idempotent. Vector store must be rebuilt after any data update.

## SQLite Schema

| Table | Purpose |
|-------|---------|
| `redfin_metrics` | Monthly metro-level housing metrics (~20 metros × 18 months) |
| `fred_metrics` | Weekly mortgage rate data (MORTGAGE30US) |
| `ai_tooltips` | Cached AI hover tooltips keyed by (metro, period, metric) |
| `ai_briefs` | Cached daily market briefs keyed by (metro, date) |
| `feedback` | 👍/👎 on RAG answers |

`ai_tooltips`, `ai_briefs`, `feedback` are auto-created on first run.

## RAG Engine (`backend/rag.py`)

- **Class:** `RAGEngine(cfg=Config(...))`
- **Models:** `claude-opus-4-6` (4096 tokens); tooltip engine uses 60 tokens
- **Retrieval:** ChromaDB top-5, collection `housing_market`
- **Metro detection:** Alias map (full name / city / city+state) pre-sorted longest-first in `__init__`; auto-detects metros from question text
- **Multi-metro:** Retrieves top-K per metro independently, deduplicates
- **Guardrails:** Rejects future predictions and property valuations (early return)
- **Confidence:** `high` (≥3 market docs), `medium` (≥1), `low` (0 → early return)
- **Grounding:** Metric definitions doc cached in `self._cached_metric_def` at init; appended to every query context
- **Citations:** Enforces `[1]`, `[2]` markers; injects fallback if Claude omits them
- **Feedback:** `_ensure_feedback_table()` called in `__init__`; `log_feedback()` just inserts

## Frontend (`frontend/app.py`)

- **Caching:** `@st.cache_data(ttl=3600)` on all DB-read functions; `@st.cache_resource` on RAGEngine instances; `_ensure_ai_tables()` guarded by session state flag
- **Sidebar:** Title, last-updated date, metro selectbox
- **Metric cards:** 5 cards (Median Sale Price, Days on Market, Inventory, Price Drop %, 30yr Mortgage); `delta_color="inverse"` on the four "down is good" metrics
- **Trend chart:** Plotly with teal line, fill-to-zero, transparent bg, styled hover tooltips
- **Tooltips:** Progress bar shown during generation; all 12 points batch-checked then lazy-generated; cached in `ai_tooltips`
- **AI Brief:** Generated once per metro per day; cached in `ai_briefs`; rendered in dark card with teal left border
- **Chat:** Metro-scoped history (`chat_histories` dict in session state); capped at 50 messages; feedback buttons `👍`/`👎` with metro-scoped keys
- **Citation stripping:** Always use `_answer_with_superscript_citations()` — do not inline `re.sub` calls

## Key Conventions

- Redfin filter: "All Residential" + "metro" region only; top 20 metros by homes sold over 18 months
- FRED data: weekly → monthly average in `update_vectors.py` before embedding
- Do not add `load_dotenv()` calls outside `RAGEngine.__init__`
- Do not call `_get_metric_definition_doc()` in query path — use `self._cached_metric_def`
- Write-path DB calls use raw `sqlite3.connect()`; read-path uses `_db_read_df` / `_db_read_scalar`
