#!/usr/bin/env bash
# -------------------------------------------------------
# HomeSignal — Local Data Setup for Streamlit Cloud Deploy
# -------------------------------------------------------
# Run this locally BEFORE deploying to Streamlit Cloud.
# Streamlit Cloud has no persistent disk, so the database
# and vector store must be committed to the repo.
#
# Prerequisites:
#   - Python 3.11+ with venv
#   - .env file with ANTHROPIC_API_KEY and FRED_API_KEY
#   - Redfin TSV at data/raw/redfin_metro_market_tracker.tsv000.gz
#     (download from https://www.redfin.com/news/data-center/)
#
# Usage:
#   chmod +x scripts/setup_data.sh
#   ./scripts/setup_data.sh
# -------------------------------------------------------

set -euo pipefail

echo "=== HomeSignal Data Setup ==="

# Check .env
if [ ! -f .env ]; then
    echo "ERROR: .env file not found. Create one with:"
    echo "  ANTHROPIC_API_KEY=sk-ant-..."
    echo "  FRED_API_KEY=your-fred-key"
    exit 1
fi

# Check Redfin file
REDFIN_FILE="data/raw/redfin_metro_market_tracker.tsv000.gz"
if [ ! -f "$REDFIN_FILE" ]; then
    echo "ERROR: Redfin data file not found at $REDFIN_FILE"
    echo "Download it from: https://www.redfin.com/news/data-center/"
    echo "  -> Housing Market Data -> Metro-level (TSV.GZ)"
    exit 1
fi

# Set up venv if needed
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

echo "Installing dependencies..."
source venv/bin/activate
pip install -q -r requirements.txt

echo ""
echo "=== Step 1/3: Ingesting FRED data ==="
python pipeline/ingest_fred.py

echo ""
echo "=== Step 2/3: Ingesting Redfin data ==="
python pipeline/ingest_redfin.py

echo ""
echo "=== Step 3/3: Building vector store ==="
python pipeline/update_vectors.py

echo ""
echo "=== Data setup complete! ==="
echo ""
echo "Next steps for Streamlit Cloud deployment:"
echo "  1. git add data/homesignal.db data/chroma_db/"
echo "  2. git commit -m 'Add pre-built data for Streamlit Cloud'"
echo "  3. git push"
echo "  4. Go to https://share.streamlit.io"
echo "  5. Connect your repo, set main file to: frontend/app.py"
echo "  6. Add secrets: ANTHROPIC_API_KEY"
echo "  7. Deploy!"
