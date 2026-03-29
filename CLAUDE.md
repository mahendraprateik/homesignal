# HomeSignal — CLAUDE.md

## Project Overview

HomeSignal is an AI-powered housing market intelligence app. Users select a metro area and see real-time metrics, a 12-month price trend chart, a daily AI market brief, and can ask questions via a RAG-powered chat interface.

## Architecture

```
homesignal/
├── home_signal_frontend/       # Decoupled Streamlit frontend
│   ├── app.py                  # Streamlit UI (imports only backend.api + formatting)
│   └── formatting.py           # Pure display helpers (no DB/backend deps)
├── backend/
│   ├── api.py                  # Backend API — single entry point for all frontend calls
│   ├── rag.py                  # RAGEngine — retrieval + Claude inference
│   └── chat_engine.py          # Hybrid RAG + SQL tool-use engine
├── pipeline/
│   ├── data_ingestion.py       # Unified: download, clean, load all data → SQLite
│   ├── refresh.py              # Freshness checks + orchestrates ingestion + vector rebuild
│   └── update_vectors.py       # SQLite → ChromaDB vector store (Redfin only; FRED via SQL)
├── data/
│   ├── homesignal.db           # SQLite: all tables
│   ├── chroma_db/              # ChromaDB persistent vectors
│   └── raw/                    # Redfin TSV.GZ source file
├── .streamlit/config.toml      # Streamlit theme (teal/off-white)
├── .env                        # ANTHROPIC_API_KEY, FRED_API_KEY (never commit)
└── verify_keys.py              # Test API key validity
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
| Data Sources | FRED API (mortgage rates, CPI, unemployment, housing starts), Redfin TSV (7 geography levels) |
| Package Manager | pip + venv |

## Environment & Run

```bash
source venv/bin/activate
python verify_keys.py                          # confirm API keys
./venv/bin/streamlit run home_signal_frontend/app.py  # http://localhost:8501
```

## Data Pipeline

```bash
# One command: download + clean + load all data into SQLite
python pipeline/data_ingestion.py

# Or selectively:
python pipeline/data_ingestion.py --redfin        # Redfin only (download + ingest)
python pipeline/data_ingestion.py --fred           # FRED only
python pipeline/data_ingestion.py --download       # download Redfin files only
python pipeline/data_ingestion.py --redfin --no-download  # ingest existing files

# After data changes, rebuild vectors:
python pipeline/update_vectors.py

# Or use refresh.py for automated freshness-based pipeline:
python pipeline/refresh.py           # checks staleness, ingests if needed, rebuilds vectors
python pipeline/refresh.py --force   # force full refresh
```

All scripts are idempotent. Vector store must be rebuilt after any data update.
FRED data is NOT embedded in vectors — it is queried from SQLite at RAG query time.

## SQLite Schema

| Table | Purpose |
|-------|---------|
| `redfin_metrics` | Housing metrics across 7 geography levels (metro/city/county/state/neighborhood/zip/national); `region_type` column distinguishes them |
| `fred_metrics` | FRED economic series: MORTGAGE30US, CPIAUCSL, UNRATE, HOUST |
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
- **FRED context:** `_get_fred_context()` queries SQLite for latest macro data (mortgage rate, CPI, unemployment, housing starts) and injects into LLM prompt at query time — not embedded in vectors
- **Grounding:** Metric definitions doc cached in `self._cached_metric_def` at init; appended to every query context
- **Citations:** Enforces `[1]`, `[2]` markers; injects fallback if Claude omits them
- **Feedback:** `_ensure_feedback_table()` called in `__init__`; `log_feedback()` just inserts

## Backend API (`backend/api.py`)

The single interface between frontend and backend. The frontend NEVER imports `sqlite3`, `backend.rag`, or `backend.chat_engine` directly.

- **Engine singletons:** Lazy-initialized, thread-safe (`_get_rag()`, `_get_chat_engine()`, `_get_rag_tooltip()`); call `reset_engines()` after data refresh
- **Data APIs:** `get_metros()`, `get_latest_metrics_for_metro()`, `get_trend_series()`, `get_latest_mortgage_rate_with_mom()`, `get_data_freshness()`, `get_feedback_stats()`
- **AI APIs:** `get_or_create_tooltips(on_progress=callback)`, `get_or_create_daily_brief()`, `chat()`, `log_feedback()`
- **Pipeline:** `run_refresh(force)` runs ingestion + vector rebuild, then resets engine singletons

## Frontend (`home_signal_frontend/`)

- **`app.py`:** Streamlit UI; imports only `backend.api` and `home_signal_frontend.formatting`
- **`formatting.py`:** Pure display helpers (no DB/Streamlit deps): `format_money`, `format_number`, `format_pct`, `normalize_price_drop_pct_for_display`, `answer_with_superscript_citations`, `render_chat_answer_preserving_dollars`, `truncate_tooltip_text`
- **Caching:** `@st.cache_data(ttl=3600)` wrappers around API calls; `_ensure_ai_tables()` guarded by session state flag
- **Sidebar:** Title, last-updated date, metro selectbox
- **Metric cards:** 5 cards (Median Sale Price, Days on Market, Inventory, Price Drop %, 30yr Mortgage); `delta_color="inverse"` on the four "down is good" metrics
- **Trend chart:** Plotly with teal line, fill-to-zero, transparent bg, styled hover tooltips
- **Tooltips:** Progress bar shown during generation; all 12 points batch-checked then lazy-generated; cached in `ai_tooltips`
- **AI Brief:** Generated once per metro per day; cached in `ai_briefs`; rendered in dark card with teal left border
- **Chat:** Metro-scoped history (`chat_histories` dict in session state); capped at 50 messages; feedback buttons via `api.log_feedback()`
- **Citation stripping:** Always use `answer_with_superscript_citations()` from `formatting.py` — do not inline `re.sub` calls

## Key Conventions

- Redfin filter: "All Residential", non-seasonally-adjusted, last 18 months; top-N per geography (metro=20, city/county/neighborhood=100, zip=200, state/national=all)
- FRED data: stored in SQLite only; queried live at RAG time via `_get_fred_context()` — NOT embedded in ChromaDB
- All data ingestion (download, clean, load) consolidated in `pipeline/data_ingestion.py`
- Do not add `load_dotenv()` calls outside `RAGEngine.__init__`
- Do not call `_get_metric_definition_doc()` in query path — use `self._cached_metric_def`
- Write-path DB calls use raw `sqlite3.connect()`; read-path uses `_db_read_df` / `_db_read_scalar`
