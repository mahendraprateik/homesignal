"""
HomeSignal Chat Engine — hybrid RAG + SQL tool-use layer.

Architecture (Option A: RAG before, SQL during):
1. RAG retrieval runs first — context docs are always injected into the prompt
2. SQL tools are available to Claude during generation via tool_use API
3. Claude sees RAG context (trends, history, metric definitions) AND can call
   SQL tools when it needs precise current numbers

This module wraps RAGEngine (unchanged) and adds:
- SQL query tools (parameterized, safe) for live database lookups
- Tool-use loop so Claude can call SQL mid-generation
- Combined sources from both RAG and SQL

Usage:
    from backend.chat_engine import ChatEngine
    engine = ChatEngine()
    result = engine.chat("What is the median sale price in Phoenix?")
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from backend.rag import Config, RAGEngine


class ChatEngine:
    """
    Hybrid chat engine: RAG context + SQL tools available to Claude.

    RAGEngine handles retrieval. This class handles:
    - SQL tool definitions and execution
    - Tool-use loop with Claude
    - Merging RAG sources with SQL tool sources
    """

    _METRIC_ALLOWLIST = {
        "median_sale_price", "days_on_market", "inventory", "price_drop_pct",
        "homes_sold", "new_listings", "months_of_supply",
    }

    def __init__(self, cfg: Optional[Config] = None) -> None:
        self.cfg = cfg or Config()
        self._rag = RAGEngine(cfg=self.cfg)

        # Reuse RAGEngine's Anthropic client and metro alias map
        self._claude = self._rag._claude
        self._metro_alias_map = self._rag._metro_alias_map

        # Tracks tool sources for the current query
        self._last_tool_sources: List[str] = []

    # ---------------------------
    # Public API
    # ---------------------------

    def chat(
        self,
        question: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        metro_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Answer a user question using RAG context + SQL tools.

        Returns the same shape as RAGEngine.query():
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
                "answer": "I don't have enough data to answer that.",
                "sources": [],
                "retrieved_docs": [],
                "confidence": "low",
                "detected_metros": [],
            }

        # Step 1: Run RAG retrieval (always, for grounding context)
        # If ChromaDB connection goes stale, reconnect and retry once.
        try:
            rag_result = self._rag.query(
                question=question,
                metro_filter=metro_filter,
                conversation_history=conversation_history,
            )
        except Exception:
            self._rag = RAGEngine(cfg=self.cfg)
            self._metro_alias_map = self._rag._metro_alias_map
            rag_result = self._rag.query(
                question=question,
                metro_filter=metro_filter,
                conversation_history=conversation_history,
            )

        # If guardrails triggered (future prediction, property valuation),
        # RAG already returned the decline — pass it through.
        if self._is_guardrail_response(rag_result):
            return rag_result

        # Step 2: Build prompt with RAG context + SQL tools for Claude
        self._last_tool_sources = []

        rag_context = self._format_rag_context(rag_result)
        metro_instruction = (
            f"- The selected metro for this session is: {metro_filter}. Prioritize this metro unless the user explicitly requests another.\n"
            if metro_filter else ""
        )
        user_prompt = (
            f"User question:\n{question}\n\n"
            f"Retrieved context documents (from vector search):\n{rag_context}\n\n"
            "Instructions:\n"
            "- Use the context documents above for trends, historical data, and general market reasoning.\n"
            "- Use the database query tools for precise, up-to-date metric values when needed.\n"
            f"{metro_instruction}"
            "- You may combine both sources in a single answer.\n"
            "- Cite context documents using [1], [2], etc.\n"
            "- When using tool results, state the data period (e.g., 'as of February 2026')."
        )

        messages = self._build_messages(conversation_history, user_prompt)

        # Step 3: Call Claude with tools
        answer = self._call_claude_with_tools(messages)

        # Step 4: Merge sources and build result
        sources = rag_result.get("sources", [])
        confidence = rag_result.get("confidence", "low")

        if self._last_tool_sources:
            confidence = "high"
            sources = sources + self._last_tool_sources

        return {
            "answer": answer.strip(),
            "sources": sources,
            "retrieved_docs": rag_result.get("retrieved_docs", []),
            "confidence": confidence,
            "detected_metros": rag_result.get("detected_metros", []),
        }

    def log_feedback(
        self,
        question: str,
        answer: str,
        feedback: str,
        metro: Optional[str] = None,
    ) -> None:
        """Delegate feedback logging to RAGEngine."""
        self._rag.log_feedback(question, answer, feedback, metro)

    # ---------------------------
    # SQL tool methods
    # ---------------------------

    def _resolve_metro(self, name: str) -> Optional[str]:
        """Resolve a short metro name to the canonical Redfin name."""
        key = name.strip().lower()
        return self._metro_alias_map.get(key)

    def _sql_latest_metrics(self, metro_name: str) -> str:
        canonical = self._resolve_metro(metro_name)
        if not canonical:
            return json.dumps({"error": f"Unknown metro: {metro_name}"})

        with sqlite3.connect(self.cfg.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM redfin_metrics WHERE metro_name = ? "
                "ORDER BY period_date DESC LIMIT 1",
                (canonical,),
            ).fetchone()

        if not row:
            return json.dumps({"error": f"No data found for {canonical}"})

        d = dict(row)
        self._last_tool_sources.append(
            f"HomeSignal DB (live query): {canonical} ({d.get('period_date', '?')})"
        )
        return json.dumps(d, default=str)

    def _sql_mortgage_rate(self) -> str:
        with sqlite3.connect(self.cfg.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT period_date, value FROM fred_metrics "
                "WHERE series_id = 'MORTGAGE30US' ORDER BY period_date DESC LIMIT 1",
            ).fetchone()

        if not row:
            return json.dumps({"error": "No mortgage rate data found"})

        d = dict(row)
        self._last_tool_sources.append(
            f"HomeSignal DB (live query): FRED MORTGAGE30US ({d['period_date']})"
        )
        return json.dumps({"rate_pct": d["value"], "as_of": d["period_date"]})

    def _sql_compare_metros(self, metro_names: List[str]) -> str:
        results = {}
        for name in metro_names[:5]:
            canonical = self._resolve_metro(name)
            if not canonical:
                results[name] = {"error": f"Unknown metro: {name}"}
                continue

            with sqlite3.connect(self.cfg.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM redfin_metrics WHERE metro_name = ? "
                    "ORDER BY period_date DESC LIMIT 1",
                    (canonical,),
                ).fetchone()

            if row:
                results[canonical] = dict(row)
                self._last_tool_sources.append(
                    f"HomeSignal DB (live query): {canonical} ({dict(row).get('period_date', '?')})"
                )
            else:
                results[canonical] = {"error": "No data found"}

        return json.dumps(results, default=str)

    def _sql_top_metros_by_metric(
        self, metric: str, order: str = "DESC", limit: int = 5
    ) -> str:
        if metric not in self._METRIC_ALLOWLIST:
            return json.dumps({
                "error": f"Invalid metric: {metric}. Allowed: {sorted(self._METRIC_ALLOWLIST)}"
            })
        if order not in ("ASC", "DESC"):
            return json.dumps({"error": "order must be ASC or DESC"})
        limit = min(max(1, limit), 20)

        with sqlite3.connect(self.cfg.db_path) as conn:
            conn.row_factory = sqlite3.Row
            latest = conn.execute(
                "SELECT MAX(period_date) as latest FROM redfin_metrics"
            ).fetchone()
            if not latest or not latest["latest"]:
                return json.dumps({"error": "No data available"})
            latest_period = latest["latest"]

            # Safe: metric validated against allowlist, order validated above
            rows = conn.execute(
                f"SELECT metro_name, {metric}, period_date FROM redfin_metrics "
                f"WHERE period_date = ? AND {metric} IS NOT NULL "
                f"ORDER BY {metric} {order} LIMIT ?",
                (latest_period, limit),
            ).fetchall()

        results = [dict(r) for r in rows]
        self._last_tool_sources.append(
            f"HomeSignal DB (live query): top metros by {metric} ({latest_period})"
        )
        return json.dumps(results, default=str)

    # ---------------------------
    # Tool definitions & dispatch
    # ---------------------------

    def _tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "query_latest_metrics",
                "description": (
                    "Get the most recent housing market metrics for a specific metro area. "
                    "Returns median sale price, days on market, inventory, price drops, "
                    "homes sold, new listings, months of supply, and more."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "metro_name": {
                            "type": "string",
                            "description": "Metro area name, e.g. 'Phoenix', 'Seattle', 'Houston'",
                        }
                    },
                    "required": ["metro_name"],
                },
            },
            {
                "name": "query_mortgage_rate",
                "description": "Get the current 30-year fixed mortgage rate from Federal Reserve data.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "compare_metros",
                "description": "Compare latest housing metrics across multiple metro areas side by side.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "metro_names": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of metro names to compare (max 5)",
                        }
                    },
                    "required": ["metro_names"],
                },
            },
            {
                "name": "top_metros_by_metric",
                "description": (
                    "Rank metros by a specific metric (highest or lowest). "
                    "Use for 'which metro has the highest/lowest...' questions."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "metric": {
                            "type": "string",
                            "enum": sorted(self._METRIC_ALLOWLIST),
                            "description": "The metric to rank by",
                        },
                        "order": {
                            "type": "string",
                            "enum": ["DESC", "ASC"],
                            "description": "DESC for highest first, ASC for lowest first",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Number of results (default 5, max 20)",
                        },
                    },
                    "required": ["metric", "order"],
                },
            },
        ]

    def _dispatch_tool(self, name: str, tool_input: Dict[str, Any]) -> str:
        try:
            if name == "query_latest_metrics":
                return self._sql_latest_metrics(tool_input["metro_name"])
            elif name == "query_mortgage_rate":
                return self._sql_mortgage_rate()
            elif name == "compare_metros":
                return self._sql_compare_metros(tool_input["metro_names"])
            elif name == "top_metros_by_metric":
                return self._sql_top_metros_by_metric(
                    tool_input["metric"],
                    tool_input.get("order", "DESC"),
                    tool_input.get("limit", 5),
                )
            else:
                return json.dumps({"error": f"Unknown tool: {name}"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    # ---------------------------
    # Claude integration
    # ---------------------------

    def _system_prompt(self) -> str:
        return (
            "You are a housing market analyst for HomeSignal. "
            "You have two data sources:\n"
            "1. **Context documents** (already provided) — vector-searched trend summaries, "
            "historical snapshots, and metric definitions. Use these for trends, reasoning, "
            "and historical context. Cite them with [1], [2], etc.\n"
            "2. **Database query tools** — for precise, up-to-date metric values. Use these "
            "when the user asks about specific current numbers (prices, rates, inventory, rankings).\n\n"
            "You can use both in a single answer. For example, use a tool to get the exact "
            "current price, then reference context documents for how it has trended.\n"
            "If neither source has the answer, say you don't have enough data.\n"
            "For multi-metro comparisons, organize clearly by metro.\n"
            "Be thorough but focused; avoid repeating context verbatim."
        )

    def _build_messages(
        self,
        conversation_history: Optional[List[Dict[str, str]]],
        current_user_prompt: str,
    ) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        if conversation_history:
            max_msgs = self.cfg.max_history_turns * 2
            for turn in conversation_history[-max_msgs:]:
                role = turn.get("role", "user")
                content = turn.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": current_user_prompt})
        return messages

    def _call_claude_with_tools(self, messages: List[Dict[str, Any]]) -> str:
        msg = self._claude.messages.create(
            model=self.cfg.claude_model,
            max_tokens=self.cfg.claude_max_tokens,
            temperature=self.cfg.claude_temperature,
            system=self._system_prompt(),
            messages=messages,
            tools=self._tool_definitions(),
        )

        # No tool use — return text directly
        if msg.stop_reason != "tool_use":
            return self._extract_text(msg)

        # Tool-use loop (max 3 iterations)
        for _ in range(3):
            tool_results = []
            for block in msg.content:
                if block.type == "tool_use":
                    result_str = self._dispatch_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })

            if not tool_results:
                break

            messages = messages + [
                {"role": "assistant", "content": msg.content},
                {"role": "user", "content": tool_results},
            ]

            msg = self._claude.messages.create(
                model=self.cfg.claude_model,
                max_tokens=self.cfg.claude_max_tokens,
                temperature=self.cfg.claude_temperature,
                system=self._system_prompt(),
                messages=messages,
                tools=self._tool_definitions(),
            )

            if msg.stop_reason != "tool_use":
                break

        return self._extract_text(msg)

    # ---------------------------
    # Helpers
    # ---------------------------

    @staticmethod
    def _extract_text(msg: Any) -> str:
        if not msg.content:
            return ""
        for block in msg.content:
            if hasattr(block, "text") and isinstance(block.text, str):
                return block.text
        return str(msg.content[0])

    @staticmethod
    def _is_guardrail_response(rag_result: Dict[str, Any]) -> bool:
        """Check if RAG returned a guardrail decline (future prediction, valuation)."""
        answer = rag_result.get("answer", "")
        guardrail_phrases = [
            "I can't help with future predictions",
            "I can't provide a specific property valuation",
        ]
        return any(phrase in answer for phrase in guardrail_phrases)

    @staticmethod
    def _format_rag_context(rag_result: Dict[str, Any]) -> str:
        """Format RAG retrieved docs as a numbered context block."""
        docs = rag_result.get("retrieved_docs", [])
        sources = rag_result.get("sources", [])

        if not docs:
            return "(No context documents retrieved)"

        lines = []
        for i, (doc, source) in enumerate(zip(docs, sources), 1):
            lines.append(f"[{i}] {source}")
            lines.append(doc)
            lines.append("")
        return "\n".join(lines).strip()
