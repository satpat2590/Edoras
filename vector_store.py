#!/usr/bin/env python3
"""
Unified vector store backed by sqlite-vec.

Provides embedding storage and KNN retrieval for:
- Market intelligence (daily snapshots, trade rationales, analysis, news)
- Trade outcome embeddings (rich context for each closed trade)
- Workspace document chunks (financial docs, code, research)

All vectors live in SQLite alongside the relational data they describe.
No pickle files, no separate services — just SQL.

Usage:
    store = VectorStore(db_path="crypto_data.db")
    store.add("market_memory", row_id=42, embedding=vec, metadata={...})
    results = store.search("market_memory", query_vec, k=5)
"""

import json
import logging
import os
import sqlite3
import struct
from typing import Dict, List, Optional, Tuple

import numpy as np
import sqlite_vec

logger = logging.getLogger(__name__)

# Default embedding model config
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536


def _get_vec_connection(db_path: str) -> sqlite3.Connection:
    """Open a sqlite3 connection with sqlite-vec loaded."""
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def embed_text(text: str, api_key: str = None, model: str = EMBEDDING_MODEL) -> np.ndarray:
    """Get embedding vector from OpenAI API."""
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY not set")
    import openai
    client = openai.OpenAI(api_key=key)
    resp = client.embeddings.create(model=model, input=text[:8000])
    return np.array(resp.data[0].embedding, dtype=np.float32)


def embed_batch(texts: List[str], api_key: str = None,
                model: str = EMBEDDING_MODEL, batch_size: int = 100) -> List[np.ndarray]:
    """Embed a batch of texts efficiently."""
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY not set")
    import openai
    client = openai.OpenAI(api_key=key)
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = [t[:8000] for t in texts[i:i + batch_size]]
        resp = client.embeddings.create(model=model, input=batch)
        for item in resp.data:
            all_embeddings.append(np.array(item.embedding, dtype=np.float32))
    return all_embeddings


# ── Table definitions ─────────────────────────────────────────────────

# Each "collection" is a pair: a regular table for metadata + a vec0 virtual table.
# The rowid of the vec0 table matches the id of the metadata table.

COLLECTIONS = {
    "market_memory": {
        "dim": 1536,
        "metadata_sql": """
            CREATE TABLE IF NOT EXISTS market_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """,
        "metadata_indexes": [
            "CREATE INDEX IF NOT EXISTS idx_market_memory_date ON market_memory(date)",
            "CREATE INDEX IF NOT EXISTS idx_market_memory_category ON market_memory(category)",
        ],
    },
    "trade_outcomes_vec": {
        "dim": 1536,
        "metadata_sql": None,  # uses existing trade_outcomes table
        "metadata_indexes": [],
    },
    "workspace_chunks": {
        "dim": 3072,  # text-embedding-3-large for workspace
        "metadata_sql": """
            CREATE TABLE IF NOT EXISTS workspace_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL,
                chunk_idx INTEGER NOT NULL,
                content TEXT NOT NULL,
                category TEXT,
                has_financial_data BOOLEAN DEFAULT 0,
                entities TEXT,
                mtime REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """,
        "metadata_indexes": [
            "CREATE INDEX IF NOT EXISTS idx_workspace_category ON workspace_chunks(category)",
            "CREATE INDEX IF NOT EXISTS idx_workspace_file ON workspace_chunks(file_path)",
        ],
    },
}


class VectorStore:
    """
    sqlite-vec backed vector store.

    Each collection has:
    - A metadata table (regular SQL table with content, dates, etc.)
    - A vec0 virtual table (embedding float[N] distance_metric=cosine)

    The rowid in vec0 matches the id/rowid in the metadata table.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._api_key = os.getenv("OPENAI_API_KEY")

    def _conn(self) -> sqlite3.Connection:
        return _get_vec_connection(self.db_path)

    def ensure_collection(self, name: str):
        """Create the metadata + vec0 tables for a collection if they don't exist."""
        if name not in COLLECTIONS:
            raise ValueError(f"Unknown collection: {name}")
        spec = COLLECTIONS[name]
        conn = self._conn()
        cur = conn.cursor()

        # Metadata table
        if spec["metadata_sql"]:
            cur.execute(spec["metadata_sql"])
        for idx_sql in spec.get("metadata_indexes", []):
            cur.execute(idx_sql)

        # Vec0 virtual table
        vec_table = f"vec_{name}"
        dim = spec["dim"]
        cur.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {vec_table}
            USING vec0(embedding float[{dim}] distance_metric=cosine)
        """)
        conn.commit()
        conn.close()

    # ── Insert ────────────────────────────────────────────────────────

    def add(self, collection: str, row_id: int, embedding: np.ndarray):
        """Add an embedding for an existing metadata row."""
        vec_table = f"vec_{collection}"
        conn = self._conn()
        try:
            conn.execute(
                f"INSERT OR REPLACE INTO {vec_table}(rowid, embedding) VALUES (?, ?)",
                (row_id, embedding.astype(np.float32).tobytes()),
            )
            conn.commit()
        finally:
            conn.close()

    def add_with_metadata(self, collection: str, embedding: np.ndarray,
                          **metadata) -> int:
        """Insert metadata row + embedding in one call. Returns the row id."""
        conn = self._conn()
        try:
            cur = conn.cursor()
            # Build INSERT for metadata table
            cols = list(metadata.keys())
            placeholders = ", ".join(["?"] * len(cols))
            col_names = ", ".join(cols)
            vals = [metadata[c] for c in cols]
            cur.execute(
                f"INSERT INTO {collection}({col_names}) VALUES ({placeholders})",
                vals,
            )
            row_id = cur.lastrowid

            # Insert embedding
            vec_table = f"vec_{collection}"
            cur.execute(
                f"INSERT INTO {vec_table}(rowid, embedding) VALUES (?, ?)",
                (row_id, embedding.astype(np.float32).tobytes()),
            )
            conn.commit()
            return row_id
        finally:
            conn.close()

    # ── Search ────────────────────────────────────────────────────────

    def search(self, collection: str, query_embedding: np.ndarray,
               k: int = 10, where: str = None,
               where_params: tuple = ()) -> List[Dict]:
        """
        KNN search over a collection.

        Args:
            collection: collection name
            query_embedding: query vector
            k: number of results
            where: optional SQL WHERE clause for the metadata table
                   (e.g., "category = ? AND date >= ?")
            where_params: parameters for the WHERE clause

        Returns:
            List of dicts with metadata + distance (lower = more similar)
        """
        vec_table = f"vec_{collection}"
        conn = self._conn()
        try:
            q_bytes = query_embedding.astype(np.float32).tobytes()

            if where:
                # Two-step: filter metadata first, then KNN on filtered set
                # sqlite-vec doesn't support JOIN in MATCH queries, so we
                # pre-filter rowids and use them
                cur = conn.cursor()
                cur.execute(
                    f"SELECT id FROM {collection} WHERE {where}",
                    where_params,
                )
                valid_ids = {row[0] for row in cur.fetchall()}

                if not valid_ids:
                    return []

                # Fetch more than k to account for filtering
                fetch_k = min(k * 5, len(valid_ids))
                rows = conn.execute(
                    f"SELECT rowid, distance FROM {vec_table} "
                    f"WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                    (q_bytes, fetch_k),
                ).fetchall()

                results = []
                for rowid, dist in rows:
                    if rowid in valid_ids:
                        results.append((rowid, dist))
                        if len(results) >= k:
                            break
            else:
                rows = conn.execute(
                    f"SELECT rowid, distance FROM {vec_table} "
                    f"WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                    (q_bytes, k),
                ).fetchall()
                results = [(r[0], r[1]) for r in rows]

            if not results:
                return []

            # Fetch metadata for matched rows
            ids = [r[0] for r in results]
            dist_map = {r[0]: r[1] for r in results}
            placeholders = ",".join(["?"] * len(ids))
            meta_rows = conn.execute(
                f"SELECT * FROM {collection} WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
            col_names = [d[0] for d in conn.execute(
                f"SELECT * FROM {collection} LIMIT 0"
            ).description]

            output = []
            for row in meta_rows:
                d = dict(zip(col_names, row))
                d["distance"] = dist_map.get(d.get("id"), 999)
                d["similarity"] = 1.0 - d["distance"]  # cosine: 0=same, 2=opposite
                output.append(d)

            output.sort(key=lambda x: x["distance"])
            return output

        finally:
            conn.close()

    # ── Utilities ─────────────────────────────────────────────────────

    def count(self, collection: str) -> int:
        vec_table = f"vec_{collection}"
        conn = self._conn()
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {vec_table}").fetchone()[0]
        finally:
            conn.close()

    def delete(self, collection: str, row_id: int):
        """Delete from both metadata and vec tables."""
        vec_table = f"vec_{collection}"
        conn = self._conn()
        try:
            conn.execute(f"DELETE FROM {vec_table} WHERE rowid = ?", (row_id,))
            conn.execute(f"DELETE FROM {collection} WHERE id = ?", (row_id,))
            conn.commit()
        finally:
            conn.close()

    def embed_and_add(self, collection: str, text: str, **metadata) -> int:
        """Embed text and store with metadata. Returns row id."""
        embedding = embed_text(text, api_key=self._api_key)
        return self.add_with_metadata(collection, embedding, **metadata)


# ── Numeric similarity for market conditions ──────────────────────────

def numeric_market_similarity(current: Dict, historical: Dict,
                               weights: Dict = None) -> float:
    """
    Compute similarity between two market condition snapshots using
    weighted Euclidean distance on normalized numeric features.

    No embeddings needed — pure math on the actual values.

    Features used:
        vix: VIX level (0-80 range)
        btc_spy_corr: BTC-SPY correlation (-1 to 1)
        portfolio_value: portfolio value (normalized to % of initial)
        signal_count: number of active signals

    Returns similarity score 0-1 (1 = identical conditions).
    """
    if weights is None:
        weights = {
            "vix": 0.35,           # regime is the biggest driver
            "btc_spy_corr": 0.30,  # correlation regime matters
            "portfolio_pct": 0.15, # portfolio state
            "signal_count": 0.10,  # market activity
            "regime_match": 0.10,  # exact regime label match
        }

    distance = 0.0
    total_weight = 0.0

    # VIX: normalize to 0-1 (VIX range ~10-80)
    vix_c = current.get("vix")
    vix_h = historical.get("vix")
    if vix_c is not None and vix_h is not None:
        w = weights["vix"]
        d = abs(vix_c - vix_h) / 70.0  # normalize by typical range
        distance += w * min(d, 1.0)
        total_weight += w

    # BTC-SPY correlation: range -1 to 1, normalize to 0-1
    corr_c = current.get("btc_spy_corr")
    corr_h = historical.get("btc_spy_corr")
    if corr_c is not None and corr_h is not None:
        w = weights["btc_spy_corr"]
        d = abs(corr_c - corr_h) / 2.0
        distance += w * min(d, 1.0)
        total_weight += w

    # Portfolio value as % of initial (normalized)
    pv_c = current.get("portfolio_value", 1000)
    pv_h = historical.get("portfolio_value", 1000)
    if pv_c and pv_h:
        w = weights["portfolio_pct"]
        # Compare as ratio (1.0 = same, 0.5 = half, 2.0 = double)
        ratio = max(pv_c, pv_h) / max(min(pv_c, pv_h), 1.0)
        d = min((ratio - 1.0) / 1.0, 1.0)  # 0 if same, 1 if 2x different
        distance += w * d
        total_weight += w

    # Signal count similarity
    sc_c = len(current.get("signals", []))
    sc_h = len(historical.get("signals", []))
    w = weights["signal_count"]
    d = abs(sc_c - sc_h) / max(sc_c + sc_h, 1)
    distance += w * d
    total_weight += w

    # Regime label match (binary)
    regime_c = current.get("regime", "unknown")
    regime_h = historical.get("regime", "unknown")
    w = weights["regime_match"]
    if regime_c == regime_h:
        pass  # distance += 0
    else:
        distance += w * 1.0
    total_weight += w

    if total_weight == 0:
        return 0.5

    normalized_distance = distance / total_weight
    return 1.0 - normalized_distance  # convert distance to similarity


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Vector Store utilities")
    parser.add_argument("--init", action="store_true", help="Initialize all collections")
    parser.add_argument("--stats", action="store_true", help="Show collection stats")
    parser.add_argument("--db", type=str, default="crypto_data.db", help="Database path")
    args = parser.parse_args()

    vs = VectorStore(args.db)

    if args.init:
        for name in COLLECTIONS:
            vs.ensure_collection(name)
            print(f"Initialized collection: {name}")
        print("All collections ready")

    elif args.stats:
        for name in COLLECTIONS:
            try:
                vs.ensure_collection(name)
                n = vs.count(name)
                print(f"{name}: {n} vectors")
            except Exception as e:
                print(f"{name}: error — {e}")
    else:
        parser.print_help()
