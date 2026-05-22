"""
vector_memory.py — Persistent semantic memory using ChromaDB + fastembed

Stores all agent interactions as embeddings for cross-session recall.
Replaces ephemeral 15-min cache with permanent vector search.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings
from fastembed import TextEmbedding

logger = logging.getLogger(__name__)


class VectorMemory:
    """
    Persistent semantic memory for the agent.

    Every interaction is embedded and stored in ChromaDB.
    Later requests retrieve similar past interactions via cosine similarity.
    """

    MAX_INTERACTIONS = 10_000  # Evict oldest when exceeded
    EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # 384 dim, local, fast

    def __init__(self, persist_dir: str | Path = ".awos/memory") -> None:
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        # ChromaDB persistent client
        self.client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=ChromaSettings(anonymized_telemetry=False)
        )

        # Collection for agent interactions
        self.collection = self.client.get_or_create_collection(
            name="awos_interactions",
            metadata={"hnsw:space": "cosine"},
        )

        # Local embedding model (fastembed = onnxruntime, no PyTorch)
        self._embedder = TextEmbedding(model_name=self.EMBED_MODEL)
        logger.info("VectorMemory ready — %d interactions stored", self.collection.count())

    # ── Core API ──────────────────────────────────────────────────────

    def store(
        self,
        text: str,
        metadata: dict[str, Any],
    ) -> None:
        """Store an interaction with its embedding."""
        doc_id = self._make_id(metadata)
        embedding = self._embed(text)

        # Ensure metadata is Chroma-compatible (str/float/int only)
        clean_meta = self._clean_metadata(metadata)
        clean_meta["_stored_at"] = datetime.now().isoformat()
        clean_meta["_text_hash"] = hashlib.md5(text.encode()).hexdigest()[:8]

        self.collection.add(
            embeddings=[embedding],
            documents=[text],
            metadatas=[clean_meta],
            ids=[doc_id],
        )

        # Evict oldest if over cap
        self._enforce_cap()

    def retrieve(
        self, query: str, n_results: int = 5, max_distance: float = 0.5
    ) -> list[dict[str, Any]]:
        """Semantic search for past interactions similar to query.

        Args:
            query: Search text.
            n_results: Max items to return.
            max_distance: Cosine distance cutoff (lower = stricter). 0.5 is a
                reasonable balance for MiniLM-L6-v2 on code snippets.
        """
        if self.collection.count() == 0:
            return []

        query_embedding = self._embed(query)
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=min(n_results, self.collection.count()),
            include=["documents", "metadatas", "distances"],
        )

        # Flatten Chroma's nested return format + apply distance threshold
        output = []
        for i in range(len(results["ids"][0])):
            dist = results["distances"][0][i]
            if dist > max_distance:
                continue
            output.append({
                "id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": dist,
            })
        return output

    def get_recent(self, n: int = 10) -> list[dict[str, Any]]:
        """Return most recently stored interactions (time-ordered)."""
        if self.collection.count() == 0:
            return []

        # Get all, sort by _stored_at desc, take top n
        all_data = self.collection.get(include=["documents", "metadatas"])
        items = []
        for i in range(len(all_data["ids"])):
            items.append({
                "id": all_data["ids"][i],
                "text": all_data["documents"][i],
                "metadata": all_data["metadatas"][i],
            })

        items.sort(
            key=lambda x: x["metadata"].get("_stored_at", ""),
            reverse=True,
        )
        return items[:n]

    def stats(self) -> dict[str, Any]:
        """Memory statistics."""
        return {
            "total_interactions": self.collection.count(),
            "persist_dir": str(self.persist_dir),
            "embed_model": self.EMBED_MODEL,
            "cap": self.MAX_INTERACTIONS,
        }

    # ── Helpers ───────────────────────────────────────────────────────

    def _embed(self, text: str) -> list[float]:
        """Embed text to 384-dim vector."""
        # fastembed returns generator of embeddings
        embeddings = list(self._embedder.embed([text]))
        return embeddings[0].tolist()

    def _make_id(self, metadata: dict[str, Any]) -> str:
        """Deterministic ID from session + timestamp + hash."""
        session = str(metadata.get("session_id", "unknown"))
        ts = datetime.now().isoformat()
        raw = f"{session}::{ts}::{json.dumps(metadata, sort_keys=True)}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _clean_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """ChromaDB only accepts str/float/int metadata values."""
        clean: dict[str, Any] = {}
        for k, v in metadata.items():
            if isinstance(v, (str, int, float, bool)):
                clean[k] = v
            else:
                clean[k] = str(v)
        return clean

    def _enforce_cap(self) -> None:
        """Remove oldest interactions if over MAX_INTERACTIONS."""
        count = self.collection.count()
        if count <= self.MAX_INTERACTIONS:
            return

        to_remove = count - self.MAX_INTERACTIONS
        all_data = self.collection.get(include=["metadatas"])

        # Sort by _stored_at ascending (oldest first)
        indexed = list(enumerate(all_data["ids"]))
        indexed.sort(
            key=lambda x: all_data["metadatas"][x[0]].get("_stored_at", "")
        )

        oldest_ids = [all_data["ids"][i] for i, _ in indexed[:to_remove]]
        self.collection.delete(ids=oldest_ids)
        logger.info("Evicted %d oldest interactions (cap: %d)", to_remove, self.MAX_INTERACTIONS)
