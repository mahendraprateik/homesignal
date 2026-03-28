# HomeSignal Frontend Overhaul - Implementation Plan

**Date:** 2026-03-27
**Files to modify:** `frontend/app.py` (909 lines), create `.streamlit/config.toml` (new)
**Estimated complexity:** MEDIUM-HIGH (single file, many coordinated changes)

---

## Context

The HomeSignal frontend is a single-file Streamlit app (`frontend/app.py`) using Plotly charts, SQLite data, and Claude Haiku via RAGEngine for AI features. It currently uses 100% Streamlit defaults with no custom theme or CSS. The goal is to fix bugs, improve UX, and apply a polished "Precision Intelligence" visual design -- all within the existing file plus one new config file.

---

## Work Objectives

1. Fix two confirmed bugs (color logic, redundant arrows)
2. Improve UX (sidebar layout, scoped chat, progress bar, error handling)
3. Apply a cohesive visual theme (config.toml + CSS injection + Plotly restyling)
4. Maintain 100% existing functionality

---

## Guardrails

**Must Have:**
- All existing features continue working (metrics, chart, tooltips, brief, chat, feedback)
- No new pip dependencies
- Changes confined to `frontend/app.py` and `.streamlit/config.toml`

**Must NOT Have:**
- New Python files
- Breaking changes to data layer or RAG integration
- Any removal of existing features

---

## Task Flow (Execution Order)

Changes are ordered to avoid breaking the app at any intermediate step. Each step should leave the app runnable.

---

### Step 1: Create `.streamlit/config.toml` (new file)

**Why first:** This is additive-only, zero risk of breaking anything. Streamlit picks it up automatically on next run.

**Create file at:** `.streamlit/config.toml`

**Exact content:**
```toml
[theme]
primaryColor = "#0D7377"
backgroundColor = "#F7F6F3"
secondaryBackgroundColor = "#EDECEA"
textColor = "#1C1C1E"
font = "sans serif"
```

**Acceptance criteria:**
- File exists at `.streamlit/config.toml` relative to project root
- App launches with warm off-white background, teal accents, near-black text
- Sidebar uses `#EDECEA` secondary background automatically

---

### Step 2: Bug Fixes (lines 679-755)

Two bugs to fix in the metric cards section.

#### 2a: Fix Price Drop % color logic (lines 735-738)

**Current code (line 737):**
```python
            color = _color_for_delta(mom)
```
and line 738:
```python
            st.markdown(f"<span style='color:{color}; font-weight:600'>{_arrow_for_delta(mom)}</span>", unsafe_allow_html=True)
```

**Problem:** Rising price-drop % is bearish (more sellers cutting prices = weak market). Currently treated as bullish (green for up). Needs `negate_for_down_is_good=True` so that rising price-drop % shows red.

**Fix:** Change line 737 to:
```python
            color = _color_for_delta(mom, negate_for_down_is_good=True)
```
And line 738 to:
```python
            st.markdown(f"<span style='color:{color}; font-weight:600'>{_arrow_for_delta(mom, negate_for_down_is_good=True)}</span>", unsafe_allow_html=True)
```

#### 2b: Remove redundant delta arrows (5 instances)

`st.metric()` already renders its own delta arrow. The extra `st.markdown()` arrow below each card is a visual duplicate.

**Remove these lines entirely:**
- Lines 689-692 (Median Sale Price arrow)
- Lines 704-707 (Days on Market arrow)
- Lines 719-722 (Inventory arrow)
- Lines 735-738 (Price Drop % arrow -- the one we just fixed becomes moot since we delete it)
- Lines 752-755 (Mortgage Rate arrow)

Specifically, for each of the 5 card blocks, remove the pattern:
```python
        mom = latest.get("..._mom_pct")
        if mom is not None:
            color = _color_for_delta(mom, ...)
            st.markdown(f"<span style='color:{color}; font-weight:600'>{_arrow_for_delta(mom, ...)}</span>", unsafe_allow_html=True)
```

**But wait** -- the `st.metric` `delta` parameter does NOT apply color inversion for "down is good" metrics. Streamlit always shows positive delta as green and negative as red. This means for Days on Market, Inventory, and Mortgage Rate, the built-in delta color will be WRONG (green for increase, red for decrease -- opposite of what we want).

**Resolution:** Streamlit's `delta_color` parameter controls this. Add `delta_color="inverse"` to the three "down is good" metrics:
- `cards[1]` (Days on Market): add `delta_color="inverse"`
- `cards[2]` (Inventory): add `delta_color="inverse"`
- `cards[3]` (Price Drop %): add `delta_color="inverse"`
- `cards[4]` (Mortgage Rate): add `delta_color="inverse"`

**Example for Days on Market (cards[1]):**
```python
    with cards[1]:
        st.metric(
            "Days on Market",
            _format_number(latest.get("days_on_market")),
            delta=(
                None
                if latest.get("days_on_market_mom_pct") is None
                else f"{latest['days_on_market_mom_pct']:.2f}%"
            ),
            delta_color="inverse",
        )
```

**Acceptance criteria:**
- No duplicate arrows below metric cards
- Price Drop % increase shows red (bearish)
- Days on Market decrease shows green (bullish)
- Inventory decrease shows green (bullish)
- Mortgage Rate decrease shows green (bullish)
- Median Sale Price retains default color behavior (up = green)

---

### Step 3: UX Fixes (structural changes to `main()`)

#### 3a: Move metro selector to sidebar (line 634-654)

**Current flow (lines 634-654):**
```python
def main() -> None:
    st.set_page_config(page_title="HomeSignal", layout="wide")
    _ensure_ai_tables()
    st.title("HomeSignal")
    st.subheader("AI-powered housing market intelligence")
    last_updated = get_last_updated_date()
    if last_updated:
        st.caption(f"Last updated: {last_updated}")
    metros_df = get_metros()
    metro_names = metros_df["metro_name"].tolist()
    default_metro = "Phoenix, AZ metro area"
    if default_metro not in metro_names and metro_names:
        default_metro = metro_names[0]
    metro_name = st.selectbox("Select a metro", metro_names, ...)
    state = metros_df.loc[...]
    rag = get_rag()
```

**New flow:**
```python
def main() -> None:
    st.set_page_config(page_title="HomeSignal", page_icon="🏠", layout="wide")
    _ensure_ai_tables()

    # --- Sidebar ---
    st.sidebar.title("HomeSignal")
    st.sidebar.caption("AI-powered housing market intelligence")

    last_updated = get_last_updated_date()
    if last_updated:
        st.sidebar.caption(f"Last updated: {last_updated}")

    metros_df = get_metros()
    metro_names = metros_df["metro_name"].tolist()

    if not metro_names:
        st.error("No metro areas found in the database. Please run the data pipeline first.")
        return

    default_metro = "Phoenix, AZ metro area"
    if default_metro not in metro_names:
        default_metro = metro_names[0]

    metro_name = st.sidebar.selectbox(
        "Select a metro area",
        metro_names,
        index=metro_names.index(default_metro),
    )
    state = metros_df.loc[metros_df["metro_name"] == metro_name, "state"].iloc[0]

    # --- Main content header ---
    st.markdown(f"<h1 class='page-title'>{metro_name}</h1>", unsafe_allow_html=True)

    rag = get_rag()
```

**Key changes:**
- `page_icon="🏠"` added to `set_page_config`
- Title, subtitle, last-updated, and selectbox move to `st.sidebar`
- Empty `metro_names` guard added (shows `st.error` instead of crash)
- Main content area now shows the selected metro as the page heading
- The old `st.title("HomeSignal")` and `st.subheader(...)` are removed from main content

#### 3b: Metro-scoped chat history (line 817-818)

**Current:**
```python
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []
```

**New:**
```python
    # Metro-scoped chat history
    if "chat_histories" not in st.session_state:
        st.session_state.chat_histories = {}
    if metro_name not in st.session_state.chat_histories:
        st.session_state.chat_histories[metro_name] = []
    chat_history = st.session_state.chat_histories[metro_name]
```

Then replace ALL references to `st.session_state.chat_history` in the chat section with `chat_history` (local variable pointing to the metro-specific list). There are references at:
- Line 821: `for i, msg in enumerate(st.session_state.chat_history):` -> `for i, msg in enumerate(chat_history):`
- Line 855: `st.session_state.chat_history[i] = msg` -> `chat_history[i] = msg`
- Line 866: `st.session_state.chat_history[i] = msg` -> `chat_history[i] = msg`
- Line 872: `st.session_state.chat_history.append(...)` -> `chat_history.append(...)`
- Line 895: `st.session_state.chat_history.append(...)` -> `chat_history.append(...)`

#### 3c: Cap chat history at 50 messages

After the chat_history initialization block (3b), add:
```python
    # Cap chat history at 50 messages
    if len(chat_history) > 50:
        st.session_state.chat_histories[metro_name] = chat_history[-50:]
        chat_history = st.session_state.chat_histories[metro_name]
```

#### 3d: Tooltip progress bar (lines 769-772)

**Current:**
```python
    with st.spinner("Preparing AI tooltips for hover..."):
        tooltip_texts, _generated_ats = get_or_create_tooltips(
            rag=rag, metro_name=metro_name, state=state, trend_df=trend_df
        )
```

**New approach:** Replace the single spinner with a progress bar. This requires modifying `get_or_create_tooltips` to accept an optional progress callback OR inlining the tooltip generation loop in `main()`.

**Recommended: Add a progress callback parameter to `get_or_create_tooltips`.**

Change the function signature (line 519-521) to:
```python
def get_or_create_tooltips(
    rag: RAGEngine, metro_name: str, state: Optional[str], trend_df: pd.DataFrame,
    progress_callback=None,
) -> Tuple[List[str], List[str]]:
```

Inside the for-loop (line 533), after processing each row, call the callback:
```python
    for idx, (_, row) in enumerate(trend_df.iterrows()):
        # ... existing logic ...
        if progress_callback:
            progress_callback(idx + 1, len(trend_df))
```

And remove the per-item `st.spinner` on line 543 (replace with a pass or just remove the `with st.spinner` wrapper, keeping the generation call).

In `main()`, replace lines 769-772 with:
```python
    progress_bar = st.progress(0, text="Generating AI tooltips...")
    def _tooltip_progress(current, total):
        progress_bar.progress(current / total, text=f"Generating AI tooltips ({current}/{total})...")
    tooltip_texts, _generated_ats = get_or_create_tooltips(
        rag=rag, metro_name=metro_name, state=state, trend_df=trend_df,
        progress_callback=_tooltip_progress,
    )
    progress_bar.empty()
```

#### 3e: Error boundaries for DB connection

Wrap the DB-dependent section in `main()` (from `get_latest_metrics_for_metro` onward) in a try/except:

At the top of `main()`, after the sidebar setup and before `latest = get_latest_metrics_for_metro(...)`, add:
```python
    try:
        latest = get_latest_metrics_for_metro(metro_name)
        # ... rest of main content ...
    except sqlite3.OperationalError as e:
        st.error(f"Database error: unable to load metrics. Please ensure the database exists at `{DB_PATH}`.")
        st.exception(e)
        return
    except Exception as e:
        st.error("An unexpected error occurred while loading data.")
        st.exception(e)
        return
```

Also wrap the initial `_ensure_ai_tables()` and `get_metros()` calls:
```python
    try:
        _ensure_ai_tables()
    except Exception as e:
        st.error(f"Failed to initialize database tables: {e}")
        return

    # ... sidebar setup ...

    try:
        metros_df = get_metros()
    except Exception as e:
        st.error(f"Failed to load metro areas from database: {e}")
        return
```

#### 3f: Feedback rerun fix (lines 856, 867)

**Current:** `st.rerun()` causes scroll-to-top.

**Fix:** Use a session_state flag to record feedback without rerun. Replace the feedback button blocks:

```python
                if msg.get("feedback") is None:
                    fb_key = f"fb_{metro_name}_{i}"
                    cols = st.columns([1, 1, 6])
                    with cols[0]:
                        if st.button("👍", key=f"up_{fb_key}"):
                            rag.log_feedback(
                                question=msg.get("question", ""),
                                answer=msg["content"],
                                feedback="up",
                                metro=metro_name,
                            )
                            msg["feedback"] = "up"
                            chat_history[i] = msg
                            st.rerun()
                    with cols[1]:
                        if st.button("👎", key=f"down_{fb_key}"):
                            rag.log_feedback(
                                question=msg.get("question", ""),
                                answer=msg["content"],
                                feedback="down",
                                metro=metro_name,
                            )
                            msg["feedback"] = "down"
                            chat_history[i] = msg
                            st.rerun()
```

**Note:** Streamlit fundamentally requires `st.rerun()` to update displayed state after button clicks. The scroll-to-top is a known Streamlit limitation. We keep `st.rerun()` but make the button columns narrower (3-column layout with spacer) for a cleaner look, and add metro-scoping to the key to avoid key collisions across metros.

**Acceptance criteria for all Step 3 items:**
- Metro selector appears in sidebar with title and last-updated
- Empty metro list shows friendly error, not traceback
- Chat history is scoped per metro (switching metros shows different histories)
- Chat capped at 50 messages
- Tooltip generation shows a progress bar instead of repeated spinners
- DB errors show friendly messages
- Feedback buttons use emoji icons, scoped keys

---

### Step 4: CSS Injection (top of `main()`, after `set_page_config`)

Insert a large `st.markdown("<style>...</style>", unsafe_allow_html=True)` block immediately after `st.set_page_config(...)` in `main()`.

**Full CSS block to inject:**

```python
    st.markdown("""
    <style>
    /* ===== Google Fonts ===== */
    @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Instrument+Serif&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');

    /* ===== Base Typography ===== */
    html, body, [class*="css"] {
        font-family: 'Plus Jakarta Sans', sans-serif;
    }

    /* Page title (used via st.markdown with class) */
    .page-title {
        font-family: 'Instrument Serif', serif !important;
        font-size: 2.4rem !important;
        font-weight: 400 !important;
        color: #1C1C1E !important;
        margin-bottom: 0.25rem !important;
        padding-top: 0.5rem !important;
    }

    /* Section headers (st.subheader) */
    h2, h3, [data-testid="stSubheader"] {
        font-family: 'Instrument Serif', serif !important;
        color: #1C1C1E !important;
    }

    /* ===== Metric Cards ===== */
    [data-testid="stMetric"] {
        background-color: #FFFFFF;
        border: 1px solid #E8E6E1;
        border-radius: 12px;
        padding: 1rem 1.25rem;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
    }

    /* Metric value in DM Mono */
    [data-testid="stMetricValue"] {
        font-family: 'DM Mono', monospace !important;
        font-weight: 500 !important;
        font-size: 1.5rem !important;
    }

    /* Metric label */
    [data-testid="stMetricLabel"] {
        font-family: 'Plus Jakarta Sans', sans-serif !important;
        font-weight: 600 !important;
        font-size: 0.8rem !important;
        text-transform: uppercase !important;
        letter-spacing: 0.04em !important;
        color: #6B6B6B !important;
    }

    /* ===== Sidebar ===== */
    [data-testid="stSidebar"] {
        border-right: none !important;
    }

    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h1 {
        font-family: 'Instrument Serif', serif !important;
        font-size: 1.8rem !important;
    }

    /* ===== AI Brief Card (dark treatment) ===== */
    .ai-brief-card {
        background-color: #1C1C1E;
        color: #F7F6F3;
        border-radius: 12px;
        padding: 1.5rem 2rem;
        border-left: 4px solid #0D7377;
        margin: 1rem 0;
    }

    .ai-brief-card p {
        color: #E8E6E1 !important;
        font-size: 0.95rem;
        line-height: 1.7;
    }

    .ai-brief-card .brief-timestamp {
        color: #6B6B6B;
        font-size: 0.75rem;
        margin-top: 0.75rem;
    }

    /* ===== Chat Messages ===== */
    [data-testid="stChatMessage"] {
        background-color: #FFFFFF;
        border: 1px solid #E8E6E1;
        border-radius: 12px;
        padding: 1rem;
        margin-bottom: 0.5rem;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
    }

    /* ===== Dividers ===== */
    hr {
        border-color: #E8E6E1 !important;
        opacity: 0.5;
    }

    /* ===== Expander (Sources) ===== */
    [data-testid="stExpander"] {
        border: 1px solid #E8E6E1 !important;
        border-radius: 8px !important;
    }

    /* ===== Progress bar ===== */
    [data-testid="stProgress"] > div > div {
        background-color: #0D7377 !important;
    }

    /* ===== Responsive: 5-column metric grid ===== */
    @media (max-width: 768px) {
        [data-testid="stHorizontalBlock"] {
            flex-wrap: wrap !important;
        }
        [data-testid="stHorizontalBlock"] > div {
            flex: 1 1 45% !important;
            min-width: 140px !important;
        }
    }
    </style>
    """, unsafe_allow_html=True)
```

**Acceptance criteria:**
- Google Fonts load (DM Mono, Instrument Serif, Plus Jakarta Sans)
- Metric cards have white bg, border, rounded corners, subtle shadow
- Section headers use Instrument Serif
- Metric values use DM Mono monospace
- Sidebar has no right border
- AI Brief renders in a dark card (Step 5 handles the HTML)
- Chat messages have card-like appearance
- 5-column layout wraps gracefully on narrow viewports

---

### Step 5: AI Brief Dark Card Treatment (lines 803-810)

**Current:**
```python
    st.subheader("Daily AI Market Brief")
    brief = get_or_create_daily_brief(rag, metro_name=metro_name, state=state)
    brief_text = brief["brief_text"]
    brief_text = re.sub(r"\[\d+\]", "", brief_text)
    brief_text = re.sub(r"<sup>\d+</sup>", "", brief_text)
    brief_text = brief_text.strip()
    st.write(brief_text)
    st.caption(f"Generated: {brief['generated_at']}")
```

**New:**
```python
    st.subheader("Daily AI Market Brief")
    brief = get_or_create_daily_brief(rag, metro_name=metro_name, state=state)
    brief_text = brief["brief_text"]
    brief_text = re.sub(r"\[\d+\]", "", brief_text)
    brief_text = re.sub(r"<sup>\d+</sup>", "", brief_text)
    brief_text = brief_text.strip()
    # Escape for safe HTML rendering, preserve dollar signs
    brief_escaped = html.escape(brief_text).replace("$", "&#36;")
    st.markdown(
        f"""<div class="ai-brief-card">
            <p>{brief_escaped}</p>
            <div class="brief-timestamp">Generated: {html.escape(brief['generated_at'])}</div>
        </div>""",
        unsafe_allow_html=True,
    )
```

**Acceptance criteria:**
- AI Brief renders in a dark charcoal card with teal left border
- Text is light-colored and readable
- Timestamp is subdued gray
- Dollar signs do not trigger LaTeX rendering

---

### Step 6: Plotly Chart Redesign (lines 774-798)

**Current:**
```python
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=trend_df["period_date"],
            y=trend_df["median_sale_price"],
            mode="lines+markers",
            customdata=[[t] for t in tooltip_texts],
            hovertemplate=(...),
        )
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        height=420,
        xaxis_title="Month",
        yaxis_title="Median Sale Price",
    )
```

**New:**
```python
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=trend_df["period_date"],
            y=trend_df["median_sale_price"],
            mode="lines+markers",
            line=dict(color="#0D7377", width=2.5),
            marker=dict(color="#0D7377", size=5),
            fill="tozeroy",
            fillcolor="rgba(13, 115, 119, 0.08)",
            customdata=[[t] for t in tooltip_texts],
            hovertemplate=(
                "<b>%{x}</b><br>"
                "Median Sale Price: %{y:$,.0f}<br>"
                "<i>%{customdata[0]}</i><extra></extra>"
            ),
        )
    )
    fig.update_layout(
        margin=dict(l=10, r=10, t=10, b=10),
        height=420,
        xaxis_title="Month",
        yaxis_title="Median Sale Price",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Plus Jakarta Sans, sans-serif", color="#1C1C1E"),
        xaxis=dict(showgrid=False, linecolor="#E8E6E1"),
        yaxis=dict(gridcolor="#E8E6E1", gridwidth=0.5, linecolor="#E8E6E1"),
        hoverlabel=dict(bgcolor="#1C1C1E", font_color="white", bordercolor="#0D7377"),
    )
```

**Acceptance criteria:**
- Chart has teal line with subtle fill-to-zero gradient
- Background is transparent (matches page bg)
- Only horizontal grid lines, subtle color
- Hover tooltip has dark bg with white text and teal border
- Font matches the app theme
- Small filled circle markers

---

## Verification Checklist

After all changes, verify:

- [ ] App launches without errors: `./venv/bin/streamlit run frontend/app.py`
- [ ] `.streamlit/config.toml` is picked up (warm off-white bg, teal accents)
- [ ] Sidebar shows: title, subtitle, last-updated, metro selector
- [ ] Main content shows selected metro name as page heading
- [ ] 5 metric cards display with styled cards (white bg, border, rounded)
- [ ] No duplicate arrows below metrics
- [ ] Price Drop % increase shows red delta
- [ ] Days on Market / Inventory / Mortgage decrease shows green delta
- [ ] Tooltip generation shows progress bar, not repeated spinners
- [ ] Chart has teal line, fill, transparent bg, styled hover
- [ ] AI Brief renders in dark card with teal left border
- [ ] Chat history is scoped per metro
- [ ] Switching metros shows different (or empty) chat history
- [ ] Chat capped at 50 messages
- [ ] Feedback buttons work (emoji icons)
- [ ] Empty DB shows friendly error message
- [ ] Google Fonts render (check DM Mono on metric values, Instrument Serif on headers)
- [ ] Layout wraps reasonably on narrower browser windows
- [ ] No Python tracebacks visible to user under normal operation

---

## Success Criteria

- All 12 changes from the specification are implemented
- Zero new pip dependencies
- Only `frontend/app.py` modified and `.streamlit/config.toml` created
- App is visually cohesive with the "Precision Intelligence" design language
- All existing features (metrics, chart, tooltips, brief, chat, feedback) work identically in terms of data/logic
