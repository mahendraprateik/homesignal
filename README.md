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
