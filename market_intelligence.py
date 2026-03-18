#!/usr/bin/env python3
"""
Market Intelligence Store — semantic vector memory for trading context.

Stores daily market snapshots, analysis notes, trade rationales, and news summaries
with embeddings for retrieval by the trading agent.

Backend: sqlite-vec (KNN search) + SQLite metadata tables.
Replaces the old BLOB-based brute-force cosine similarity approach.
"""

import os
import sys
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DB_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class MarketIntelligence:
    """Semantic vector store for market context and trading memory."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._openai_client = None
        self._vs = None
        self._ensure_tables()

    @property
    def vs(self):
        """Lazy-load VectorStore to avoid import overhead."""
        if self._vs is None:
            from vector_store import VectorStore
            self._vs = VectorStore(self.db_path)
            self._vs.ensure_collection("market_memory")
        return self._vs

    def _ensure_tables(self):
        """Ensure metadata table exists (vec0 table created by VectorStore)."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        # Keep the same schema as before, but drop the embedding BLOB column
        # for new rows. Old rows with BLOB embeddings are migrated on first use.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS market_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding BLOB,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_market_memory_date ON market_memory(date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_market_memory_category ON market_memory(category)")
        conn.commit()
        conn.close()

    @property
    def openai_client(self):
        if self._openai_client is None:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY not set")
            import openai
            self._openai_client = openai.OpenAI(api_key=api_key)
        return self._openai_client

    # ── Embedding ────────────────────────────────────────────────────────

    def _embed(self, text: str) -> np.ndarray:
        """Get embedding vector for text."""
        resp = self.openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=text[:8000],
        )
        return np.array(resp.data[0].embedding, dtype=np.float32)

    # ── Storage ──────────────────────────────────────────────────────────

    def store(self, content: str, category: str, date: str = None, metadata: dict = None):
        """Store a piece of market intelligence with its embedding in sqlite-vec."""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        # Insert metadata row first
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO market_memory (date, category, content, metadata) "
            "VALUES (?, ?, ?, ?)",
            (date, category, content, json.dumps(metadata or {})),
        )
        row_id = cur.lastrowid
        conn.commit()
        conn.close()

        # Embed and store vector
        try:
            embedding = self._embed(content)
            self.vs.add("market_memory", row_id, embedding)
        except Exception as e:
            logger.warning(f"Embedding failed for row {row_id}: {e}")

        logger.info(f"Stored {category} memory for {date} ({len(content)} chars)")

    def store_daily_snapshot(self, snapshot: dict):
        """Store a structured daily market snapshot."""
        date = snapshot.get("date", datetime.now().strftime("%Y-%m-%d"))

        summary_parts = [f"Market snapshot for {date}."]
        if "regime" in snapshot:
            summary_parts.append(f"VIX regime: {snapshot['regime']}.")
        if "vix" in snapshot:
            summary_parts.append(f"VIX level: {snapshot['vix']:.1f}.")
        if "btc_price" in snapshot:
            summary_parts.append(f"BTC price: ${snapshot['btc_price']:.0f}.")
        if "btc_spy_corr" in snapshot:
            summary_parts.append(f"BTC-SPY correlation: {snapshot['btc_spy_corr']:.3f}.")
        if "portfolio_value" in snapshot:
            summary_parts.append(f"Portfolio value: ${snapshot['portfolio_value']:.2f}.")
        if "signals" in snapshot:
            for sig in snapshot["signals"]:
                summary_parts.append(f"{sig['symbol']}: {sig['action']} signal (strength {sig['strength']:.0f}).")
        if "trades_executed" in snapshot:
            for trade in snapshot["trades_executed"]:
                summary_parts.append(f"Executed: {trade['side']} {trade['symbol']} ${trade.get('amount', 0):.2f}.")
        if "analysis" in snapshot:
            summary_parts.append(snapshot["analysis"])

        content = " ".join(summary_parts)
        self.store(content, "daily_snapshot", date, metadata=snapshot)

    def store_trade_rationale(self, symbol: str, action: str, rationale: str, date: str = None):
        """Store the reasoning behind a trade decision."""
        content = f"Trade rationale for {action} {symbol}: {rationale}"
        self.store(content, "trade_rationale", date, metadata={"symbol": symbol, "action": action})

    def store_analysis(self, title: str, analysis: str, date: str = None):
        """Store a market analysis or insight."""
        content = f"{title}: {analysis}"
        self.store(content, "analysis", date, metadata={"title": title})

    def store_news_summary(self, summary: str, symbols: list = None, date: str = None):
        """Store a news digest summary."""
        self.store(summary, "news", date, metadata={"symbols": symbols or []})

    # ── Retrieval ────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 5, category: str = None, days_back: int = 90) -> List[dict]:
        """Semantic search over market memory using sqlite-vec KNN."""
        try:
            query_emb = self._embed(query)
        except Exception as e:
            logger.warning(f"Query embedding failed: {e}")
            return []

        cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        if category:
            where = "category = ? AND date >= ?"
            where_params = (category, cutoff)
        else:
            where = "date >= ?"
            where_params = (cutoff,)

        results = self.vs.search(
            "market_memory", query_emb, k=top_k,
            where=where, where_params=where_params,
        )

        # Normalize output format (parse metadata JSON)
        for r in results:
            if isinstance(r.get("metadata"), str):
                try:
                    r["metadata"] = json.loads(r["metadata"])
                except (json.JSONDecodeError, TypeError):
                    r["metadata"] = {}

        return results

    def get_recent(self, category: str = None, days_back: int = 7, limit: int = 20) -> List[dict]:
        """Get recent memories by category (no vector search, just time-based)."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        if category:
            cur.execute(
                "SELECT date, category, content, metadata FROM market_memory "
                "WHERE category = ? AND date >= ? ORDER BY date DESC LIMIT ?",
                (category, cutoff, limit),
            )
        else:
            cur.execute(
                "SELECT date, category, content, metadata FROM market_memory "
                "WHERE date >= ? ORDER BY date DESC LIMIT ?",
                (cutoff, limit),
            )

        results = []
        for row in cur.fetchall():
            results.append({
                "date": row[0],
                "category": row[1],
                "content": row[2],
                "metadata": json.loads(row[3]) if row[3] else {},
            })
        conn.close()
        return results

    def find_similar_conditions(self, current_snapshot: dict, top_k: int = 3) -> List[dict]:
        """
        Find historical days with similar market conditions.

        Uses NUMERIC similarity on actual values (VIX, correlation, regime)
        instead of embedding text descriptions. Faster, free, more accurate.
        """
        from vector_store import numeric_market_similarity

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        cur.execute(
            "SELECT id, date, content, metadata FROM market_memory "
            "WHERE category = 'daily_snapshot' AND date >= ? ORDER BY date DESC",
            (cutoff,),
        )

        results = []
        for row in cur.fetchall():
            meta = json.loads(row[3]) if row[3] else {}
            # Build historical snapshot from stored metadata
            historical = {
                "regime": meta.get("regime", "unknown"),
                "vix": meta.get("vix"),
                "btc_spy_corr": meta.get("btc_spy_corr"),
                "portfolio_value": meta.get("portfolio_value"),
                "signals": meta.get("signals", []),
            }
            sim = numeric_market_similarity(current_snapshot, historical)
            results.append({
                "id": row[0],
                "date": row[1],
                "content": row[2],
                "metadata": meta,
                "similarity": sim,
            })

        conn.close()
        results.sort(key=lambda x: x["similarity"], reverse=True)
        return results[:top_k]

    # ── Stats ────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COUNT(DISTINCT date), MIN(date), MAX(date) FROM market_memory")
        total, days, first, last = cur.fetchone()
        cur.execute("SELECT category, COUNT(*) FROM market_memory GROUP BY category")
        categories = {row[0]: row[1] for row in cur.fetchall()}
        conn.close()

        # Vector count
        try:
            vec_count = self.vs.count("market_memory")
        except Exception:
            vec_count = 0

        return {
            "total_memories": total,
            "vectors": vec_count,
            "days_covered": days,
            "first_date": first,
            "last_date": last,
            "categories": categories,
        }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Market Intelligence Store")
    parser.add_argument("--search", type=str, help="Semantic search query")
    parser.add_argument("--recent", action="store_true", help="Show recent memories")
    parser.add_argument("--stats", action="store_true", help="Show store stats")
    parser.add_argument("--migrate", action="store_true", help="Migrate old BLOB embeddings to sqlite-vec")
    parser.add_argument("--category", type=str, help="Filter by category")
    parser.add_argument("--days", type=int, default=7, help="Days lookback")
    args = parser.parse_args()

    mi = MarketIntelligence()

    if args.migrate:
        # Migrate old BLOB embeddings to sqlite-vec
        conn = sqlite3.connect(mi.db_path)
        cur = conn.cursor()
        cur.execute("SELECT id, embedding FROM market_memory WHERE embedding IS NOT NULL")
        rows = cur.fetchall()
        conn.close()
        migrated = 0
        for row_id, emb_blob in rows:
            try:
                emb = np.frombuffer(emb_blob, dtype=np.float32)
                if len(emb) == 1536:
                    mi.vs.add("market_memory", row_id, emb)
                    migrated += 1
            except Exception as e:
                logger.warning(f"Failed to migrate row {row_id}: {e}")
        print(f"Migrated {migrated}/{len(rows)} embeddings to sqlite-vec")

    elif args.search:
        results = mi.search(args.search, category=args.category, days_back=args.days)
        for r in results:
            print(f"[{r['date']}] ({r['category']}) sim={r.get('similarity', 0):.3f}")
            print(f"  {r['content'][:200]}")
            print()
    elif args.recent:
        results = mi.get_recent(category=args.category, days_back=args.days)
        for r in results:
            print(f"[{r['date']}] ({r['category']}) {r['content'][:150]}")
    elif args.stats:
        s = mi.stats()
        print(f"Total memories: {s['total_memories']}")
        print(f"Vectors in sqlite-vec: {s['vectors']}")
        print(f"Days covered: {s['days_covered']}")
        print(f"Date range: {s['first_date']} to {s['last_date']}")
        for cat, count in s.get("categories", {}).items():
            print(f"  {cat}: {count}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
