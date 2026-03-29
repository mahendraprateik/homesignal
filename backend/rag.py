"""
HomeSignal RAG backend.

This module implements a `RAGEngine` that:
1) Retrieves relevant documents from a persistent ChromaDB collection
2) Calls Anthropic Claude with retrieved context only (grounded generation)
3) Returns an answer with citations, plus confidence and retrieval details
4) Supports conversational memory (multi-turn history)
5) Auto-detects and compares multiple metros in a single question
6) Logs thumbs up/down feedback into SQLite

Model: claude-opus-4-6 with 4096-token budget.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import anthropic
import chromadb
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer


@dataclass(frozen=True)
class Config:
    # Storage
    db_path: str = "data/homesignal.db"
    chroma_dir: str = "data/chroma_db/"
    collection_name: str = "housing_market"

    # Embeddings (local)
    embedding_model_name: str = "all-MiniLM-L6-v2"

    # Retrieval
    top_k: int = 5

    # Claude - Opus 4.6 with generous token budget
    claude_model: str = "claude-opus-4-6"
    claude_max_tokens: int = 4096
    claude_temperature: float = 0.2

    # Conversation memory: number of prior turns (user+assistant pairs) to include
    max_history_turns: int = 6

    # Chroma metadata keys (must match update_vectors.py)
    meta_metro_key: str = "metro_name"
    meta_state_key: str = "state"
    meta_period_key: str = "period_date"
    meta_doc_type_key: str = "doc_type"

    metric_definition_doc_id: str = "metric_definition::v1"


class RAGEngine:
    """
    Core retrieval + grounded answer engine for HomeSignal.

    Enhancements over v1:
    - claude-opus-4-6 model with 4096-token budget
    - Conversational memory via multi-turn Claude messages
    - Multi-metro comparison: retrieves docs for each detected metro separately
    - Smarter metro detection: alias map (city name, state abbreviation variants)
    - Trend document support (doc_type="metro_trend")
    """

    def __init__(self, cfg: Optional[Config] = None) -> None:
        load_dotenv()
        self.cfg = cfg or Config()

        anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        if not anthropic_api_key:
            raise RuntimeError("FAIL: ANTHROPIC_API_KEY not found in .env")
        self._claude = anthropic.Anthropic(api_key=anthropic_api_key)

        self._embedder = SentenceTransformer(self.cfg.embedding_model_name)

        self._chroma_client = chromadb.PersistentClient(path=self.cfg.chroma_dir)
        self._collection = self._chroma_client.get_collection(
            name=self.cfg.collection_name
        )

        # Build metro alias map for smarter question-level metro detection
        self._metro_list = self._load_metro_list()
        self._metro_alias_map = self._build_metro_alias_map(self._metro_list)
        # Pre-sort aliases longest-first so _detect_metros_in_question avoids re-sorting per call
        self._sorted_aliases = sorted(self._metro_alias_map, key=len, reverse=True)

        # Cache static metric definition doc to avoid a ChromaDB round-trip per query
        self._cached_metric_def = self._get_metric_definition_doc()

        # Ensure feedback table exists once at startup
        self._ensure_feedback_table()

    # ---------------------------
    # Metro alias map
    # ---------------------------

    def _load_metro_list(self) -> List[str]:
        """Load canonical metro names from SQLite."""
        try:
            with sqlite3.connect(self.cfg.db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT DISTINCT metro_name FROM redfin_metrics")
                return [row[0] for row in cur.fetchall()]
        except Exception:
            return []

    def _build_metro_alias_map(self, metros: List[str]) -> Dict[str, str]:
        """
        Maps lowercased aliases to canonical metro names.

        For "Phoenix, AZ metro area" produces aliases:
          "phoenix, az metro area" → canonical
          "phoenix"               → canonical
          "phoenix az"            → canonical
        """
        alias_map: Dict[str, str] = {}
        for metro in metros:
            # Full name
            alias_map[metro.lower()] = metro
            # City part only: "phoenix"
            city = metro.split(",")[0].strip().lower()
            if city and city not in alias_map:
                alias_map[city] = metro
            # City + state abbreviation: "phoenix az"
            parts = metro.split(",")
            if len(parts) >= 2:
                state_token = parts[1].strip().split()[0].lower()
                combined = f"{city} {state_token}"
                if combined not in alias_map:
                    alias_map[combined] = metro
        return alias_map

    def _detect_metros_in_question(self, question: str) -> List[str]:
        """
        Returns list of canonical metro names found in the question.
        Longer aliases are checked first to prevent shorter substrings from
        masking full matches (e.g. "St. Louis" before "St.").
        """
        q_lower = question.lower()
        found: set = set()
        for alias in self._sorted_aliases:
            if alias and alias in q_lower:
                found.add(self._metro_alias_map[alias])
        return list(found)

    def _infer_metro_from_history(
        self, conversation_history: Optional[List[Dict[str, str]]]
    ) -> Optional[str]:
        """
        When a follow-up question has no metro name in it, scan recent history
        (most recent first) and return the first metro found.
        This ensures retrieval stays anchored to the ongoing conversation topic.
        """
        if not conversation_history:
            return None
        for turn in reversed(conversation_history):
            content = turn.get("content", "")
            metros = self._detect_metros_in_question(content)
            if metros:
                return metros[0]
        return None

    # ---------------------------
    # Main query entrypoint
    # ---------------------------

    def query(
        self,
        question: str,
        metro_filter: Optional[str] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Retrieve context from Chroma and generate a grounded answer via Claude.

        Args:
            question: The user's current question.
            metro_filter: If set, restricts retrieval to this metro (used by
                          tooltip/brief generators). If None, auto-detects metros
                          from the question text.
            conversation_history: Prior turns as a list of
                {"role": "user"|"assistant", "content": str} dicts.
                Included in the Claude messages for multi-turn awareness.

        Returns:
            {
              "answer": str,
              "sources": list[str],
              "retrieved_docs": list[str],
              "confidence": "high" | "medium" | "low",
              "detected_metros": list[str],
            }
        """
        question = (question or "").strip()
        if not question:
            return {
                "answer": "I don't have enough data to answer that",
                "sources": [],
                "retrieved_docs": [],
                "confidence": "low",
                "detected_metros": [],
            }

        metro_filter = metro_filter.strip() if isinstance(metro_filter, str) else None

        # --- Metro detection ---
        # Explicit filter → use as-is (tooltip/brief use case).
        # No filter → auto-detect from question, then fall back to history context.
        if metro_filter:
            detected_metros: List[str] = [metro_filter]
        else:
            detected_metros = self._detect_metros_in_question(question)
            # Follow-up questions often omit the metro name ("how does that compare?").
            # Infer from conversation history so retrieval stays on topic.
            if not detected_metros and conversation_history:
                inferred = self._infer_metro_from_history(conversation_history)
                if inferred:
                    detected_metros = [inferred]

        is_multi_metro = len(detected_metros) >= 2
        single_metro_filter: Optional[str] = detected_metros[0] if len(detected_metros) == 1 else None

        # --- Retrieval ---
        if is_multi_metro:
            retrieved = self._retrieve_multi_metro_docs(question, detected_metros)
        else:
            retrieved = self._retrieve_top_docs(question, metro_filter=single_metro_filter)

        retrieved_docs = [item["doc_text"] for item in retrieved]
        retrieved_metas = [item["metadata"] for item in retrieved]

        # Confidence: based on number of market_data docs retrieved
        market_docs = [
            m for m in retrieved_metas
            if (m or {}).get(self.cfg.meta_doc_type_key) == "market_data"
        ]
        if len(market_docs) >= 3:
            confidence = "high"
        elif len(market_docs) >= 1:
            confidence = "medium"
        else:
            confidence = "low"

        # Metric definitions doc always appended for grounding
        metric_def = self._cached_metric_def
        metric_def_source_desc = self._source_desc(metric_def["metadata"])

        # --- Guardrails ---
        if self._is_future_prediction_question(question):
            return {
                "answer": (
                    "I can't help with future predictions. I can only summarize historical "
                    "metro-level housing market data available in HomeSignal. [1]"
                ),
                "sources": [metric_def_source_desc],
                "retrieved_docs": retrieved_docs,
                "confidence": confidence,
                "detected_metros": detected_metros,
            }

        if self._is_property_valuation_question(question):
            return {
                "answer": (
                    "I can't provide a specific property valuation from this dataset. "
                    "If you share a metro/market context, I can explain metro-level trends "
                    "and what the HomeSignal metrics indicate. [1]"
                ),
                "sources": [metric_def_source_desc],
                "retrieved_docs": retrieved_docs,
                "confidence": confidence,
                "detected_metros": detected_metros,
            }

        # --- Build numbered context block for citations ---
        context_items: List[Dict[str, str]] = []
        sources: List[str] = []

        for item in retrieved:
            meta = item["metadata"] or {}
            desc = self._source_desc(meta)
            context_items.append({"source_desc": desc, "doc_text": item["doc_text"]})
            sources.append(desc)

        # Metric definitions appended last
        context_items.append(
            {"source_desc": metric_def_source_desc, "doc_text": metric_def["doc_text"]}
        )
        sources.append(metric_def_source_desc)

        context_block = self._format_context_for_claude(context_items)

        multi_metro_note = (
            f"\n- This question compares {len(detected_metros)} metros: "
            f"{', '.join(detected_metros)}. Structure your answer to address each clearly."
            if is_multi_metro else ""
        )

        user_prompt = (
            f"User question:\n{question}\n\n"
            "Context documents:\n"
            f"{context_block}\n\n"
            "Task:\n"
            "- Use the provided context as primary evidence when it is relevant.\n"
            "- You may use broader housing/economic reasoning where context is incomplete; clearly label such points as general reasoning.\n"
            f"- Include citations like [1], [2], ... for claims supported by the provided context.{multi_metro_note}"
        )

        messages = self._build_messages(conversation_history, user_prompt)
        answer = self._call_claude(system_prompt=self._system_prompt(), messages=messages)

        if not self._has_citation_markers(answer):
            citation_list = ", ".join(f"[{i}]" for i in range(1, len(context_items) + 1))
            answer = f"{answer.strip()}\nSources: {citation_list}"

        return {
            "answer": answer.strip(),
            "sources": sources,
            "retrieved_docs": retrieved_docs,
            "confidence": confidence,
            "detected_metros": detected_metros,
        }

    def log_feedback(
        self,
        question: str,
        answer: str,
        feedback: str,
        metro: Optional[str] = None,
    ) -> None:
        """
        Logs thumbs up/down feedback into SQLite table `feedback`.

        Schema:
          id INTEGER PRIMARY KEY AUTOINCREMENT
          question TEXT
          answer TEXT
          feedback TEXT  -- 'up' or 'down'
          metro TEXT
          timestamp TEXT ISO-8601
        """
        feedback_norm = (feedback or "").strip().lower()
        if feedback_norm not in ("up", "down"):
            raise ValueError("feedback must be 'up' or 'down'")

        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

        with sqlite3.connect(self.cfg.db_path) as conn:
            conn.execute(
                """
                INSERT INTO feedback (question, answer, feedback, metro, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (question, answer, feedback_norm, metro, now_iso),
            )
            conn.commit()

    def _ensure_feedback_table(self) -> None:
        with sqlite3.connect(self.cfg.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    feedback TEXT NOT NULL,
                    metro TEXT,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.commit()

    # ---------------------------
    # Retrieval
    # ---------------------------

    def _embed_text(self, text: str) -> List[float]:
        emb = self._embedder.encode(
            text,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return emb.tolist()

    def _retrieve_top_docs(
        self,
        question: str,
        metro_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query_emb = self._embed_text(question)

        where: Optional[Dict[str, Any]] = None
        if metro_filter:
            where = {self.cfg.meta_metro_key: metro_filter}

        # Tighter retrieval when filtered to one metro; broader when querying all.
        n_results = self.cfg.top_k if where else min(self.cfg.top_k * 3, 15)

        result = self._collection.query(
            query_embeddings=[query_emb],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        raw_docs = (result.get("documents") or [[]])[0]
        raw_metas = (result.get("metadatas") or [[]])[0]

        docs: List[Dict[str, Any]] = []
        for doc_text, meta in zip(raw_docs, raw_metas):
            if doc_text is None:
                continue
            docs.append({"doc_text": doc_text, "metadata": meta or {}})

        return docs

    def _retrieve_multi_metro_docs(
        self,
        question: str,
        metros: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Retrieve top_k docs independently for each metro and combine.
        Deduplicates by first 120 chars of doc text.
        """
        seen: set = set()
        combined: List[Dict[str, Any]] = []

        for metro in metros:
            docs = self._retrieve_top_docs(question, metro_filter=metro)
            for doc in docs:
                dedup_key = doc["doc_text"][:120]
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    combined.append(doc)

        return combined

    def _get_metric_definition_doc(self) -> Dict[str, Any]:
        """Fetch metric definition grounding doc; falls back to inline text if missing."""
        try:
            res = self._collection.get(
                ids=[self.cfg.metric_definition_doc_id],
                include=["documents", "metadatas"],
            )
            docs = res.get("documents") or []
            metas = res.get("metadatas") or []
            if docs and docs[0]:
                return {"doc_text": docs[0], "metadata": metas[0] if metas else {}}
        except Exception:
            pass

        fallback_text = (
            "HomeSignal metric definitions (grounding rules):\n"
            "median_sale_price: median sale price USD\n"
            "days_on_market: median days on market\n"
            "inventory: active listings count\n"
            "price_drop_pct: percentage of listings with a price reduction\n"
            "mortgage_rate_30yr: 30yr fixed rate from Federal Reserve\n"
        )
        fallback_meta = {
            self.cfg.meta_metro_key: "ALL",
            self.cfg.meta_state_key: "ALL",
            self.cfg.meta_period_key: "ALL",
            self.cfg.meta_doc_type_key: "metric_definition",
        }
        return {"doc_text": fallback_text, "metadata": fallback_meta}

    # ---------------------------
    # Claude integration
    # ---------------------------

    def _build_messages(
        self,
        conversation_history: Optional[List[Dict[str, str]]],
        current_user_prompt: str,
    ) -> List[Dict[str, str]]:
        """
        Build the Claude messages array.

        Includes the last max_history_turns * 2 messages from conversation history
        (each turn = 1 user message + 1 assistant message), followed by the
        current question with retrieved context.
        """
        messages: List[Dict[str, str]] = []

        if conversation_history:
            max_msgs = self.cfg.max_history_turns * 2
            for turn in conversation_history[-max_msgs:]:
                role = turn.get("role", "user")
                content = turn.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": current_user_prompt})
        return messages

    def _system_prompt(self) -> str:
        return (
            "You are a housing market analyst for HomeSignal. "
            "Use provided context documents and query results as your primary evidence base. "
            "You may also apply general domain knowledge when needed, but clearly distinguish it from data-backed claims. "
            "When data in context supports a claim, cite it using bracketed indices like [1], [2]. "
            "If confidence is limited, state assumptions and uncertainty explicitly. "
            "For multi-metro comparisons, organize your response clearly by metro with headers or bullets. "
            "You have access to the prior conversation — use it to resolve follow-up questions and references. "
            "Be thorough but focused; avoid repeating context verbatim."
        )

    def _format_context_for_claude(self, context_items: List[Dict[str, str]]) -> str:
        """Builds a numbered context block so citations align with [1], [2], ..."""
        chunks: List[str] = []
        for i, item in enumerate(context_items, start=1):
            chunks.append(f"[{i}] {item['source_desc']}")
            chunks.append(item["doc_text"])
            chunks.append("")
        return "\n".join(chunks).strip()

    def _call_claude(self, system_prompt: str, messages: List[Dict[str, str]]) -> str:
        msg = self._claude.messages.create(
            model=self.cfg.claude_model,
            max_tokens=self.cfg.claude_max_tokens,
            temperature=self.cfg.claude_temperature,
            system=system_prompt,
            messages=messages,
        )

        if not msg.content:
            return ""
        for block in msg.content:
            if hasattr(block, "text") and isinstance(block.text, str):
                return block.text
        return str(msg.content[0])

    def _has_citation_markers(self, text: str) -> bool:
        return re.search(r"\[\s*\d+\s*\]", text or "") is not None

    def _source_desc(self, meta: Dict[str, Any]) -> str:
        doc_type = meta.get(self.cfg.meta_doc_type_key, "market_data")
        if doc_type == "metric_definition":
            return "Metric definitions (HomeSignal)"
        metro = meta.get(self.cfg.meta_metro_key, "Unknown metro")
        if doc_type == "metro_trend":
            return f"18-month trend summary: {metro}"
        state = meta.get(self.cfg.meta_state_key, "Unknown state")
        period_date = meta.get(self.cfg.meta_period_key, "Unknown period")
        return f"Redfin/FRED snapshot: {metro}, {state} ({period_date})"

    def _is_property_valuation_question(self, question: str) -> bool:
        q = question.lower()
        valuation_keywords = [
            "valuation", "appraisal", "appraise", "estimate the value",
            "house value", "property value", "worth", "zestimate",
            "how much is", "price estimate",
        ]
        if any(k in q for k in valuation_keywords):
            return True
        address_like = any(
            token in q
            for token in [" street", " st ", " avenue", " ave ", " road", " rd ", " drive", " dr "]
        )
        return address_like

    def _is_future_prediction_question(self, question: str) -> bool:
        q = question.lower()
        future_keywords = [
            "forecast", "predict", "prediction", "expected", "future",
            "in the next", "next year", "tomorrow", "will ", "will it",
            "will prices", "will inventory", "will mortgage",
        ]
        return any(k in q for k in future_keywords)
