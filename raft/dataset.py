"""In-process semantic view of the trace store.

Everything that needs meaning rather than columns - clustering, the aspect
judge, semantic search, the follow-up planner - shares one fitted index so a
question is embedded in exactly the space the traces were embedded in. It is
rebuilt when the trace count changes, which is the only way traces arrive in
this build.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from raft.db import Database
from raft.embedding import SemanticIndex
from raft.query import DatasetCatalog


@dataclass
class TraceView:
    trace_id: str
    document: str
    row: int


class DatasetIndex:
    def __init__(
        self,
        db: Database,
        *,
        use_bge: bool = False,
        bge_model: str = "",
        bge_revision: str = "",
        encoder_factory=None,
    ):
        self.db = db
        self.use_bge = use_bge
        self.bge_model = bge_model
        self.bge_revision = bge_revision
        # Called at build time so a gateway that is down at import does not stop
        # the app from starting on the local fallback.
        self.encoder_factory = encoder_factory
        self._lock = threading.Lock()
        self._fingerprint: tuple[int, str] | None = None
        self.index: SemanticIndex | None = None
        self.trace_ids: list[str] = []
        self.documents: list[str] = []
        self.user_documents: list[str] = []
        self.user_full_documents: list[str] = []
        self.intent_documents: list[str] = []
        self.vectors: np.ndarray = np.zeros((0, 0), dtype=np.float32)
        self.user_vectors: np.ndarray = np.zeros((0, 0), dtype=np.float32)
        self.catalog: DatasetCatalog = DatasetCatalog({})
        self._position: dict[str, int] = {}
        self.encoder_error: str | None = None

    # -- lifecycle --------------------------------------------------------
    def fingerprint(self) -> tuple[int, str]:
        row = self.db.fetch_one("SELECT COUNT(*) AS count, COALESCE(MAX(id),'') AS last FROM traces")
        return (int(row["count"]), str(row["last"])) if row else (0, "")

    def ensure(self) -> None:
        current = self.fingerprint()
        if self.index is not None and current == self._fingerprint:
            return
        with self._lock:
            if self.index is not None and self.fingerprint() == self._fingerprint:
                return
            self._rebuild()
            self._fingerprint = current

    def refresh(self) -> None:
        with self._lock:
            self._rebuild()
            self._fingerprint = self.fingerprint()

    def _rebuild(self) -> None:
        rows = self.db.fetch_all(
            "SELECT id,user_request,user_text,summary,what_happened,embedding FROM traces ORDER BY id"
        )
        spans = self.db.fetch_all(
            "SELECT trace_id,type,name,content_redacted FROM spans ORDER BY trace_id,idx"
        )
        text_by_trace: dict[str, list[str]] = {}
        user_by_trace: dict[str, list[str]] = {}
        tools_by_trace: dict[str, list[str]] = {}
        for span in spans:
            bucket = text_by_trace.setdefault(span["trace_id"], [])
            if span["type"] in ("user_message", "assistant_message", "error"):
                bucket.append(span["content_redacted"])
            elif span["type"] == "tool_call" and span["name"]:
                bucket.append(f"tool {span['name']}")
                tools_by_trace.setdefault(span["trace_id"], []).append(span["name"])
            if span["type"] == "user_message":
                user_by_trace.setdefault(span["trace_id"], []).append(span["content_redacted"])

        self.trace_ids = [row["id"] for row in rows]
        self.documents = [
            " ".join([row["user_request"], *text_by_trace.get(row["id"], [])]) for row in rows
        ]
        # What the person asked for, with the agent's words removed. Layer 2
        # groups people by what they wanted, not by how the agent replied.
        # The opening ask, and only the opening ask. Later user turns are mostly
        # "that is not what I asked" - real, but shared across every unhappy
        # conversation, so they would cluster people by frustration rather than
        # by what they came for.
        self.user_documents = [
            (user_by_trace.get(row["id"]) or [row["user_request"]])[0] for row in rows
        ]
        # What the person asked plus which capability was involved. This is the
        # space Layer 2 groups in: user turns alone miss that two differently
        # worded asks hit the same tool, and adding the agent's prose groups
        # conversations by shared error boilerplate instead of by subject.
        self.intent_documents = [
            " ".join(
                (user_by_trace.get(row["id"]) or [row["user_request"]])
                + [f"tool {name}" for name in tools_by_trace.get(row["id"], [])]
            )
            for row in rows
        ]
        # Every user turn, for aspect questions that ask what the person said.
        self.user_full_documents = [
            " ".join(user_by_trace.get(row["id"]) or [row["user_text"] or row["user_request"]])
            for row in rows
        ]
        self._position = {trace_id: position for position, trace_id in enumerate(self.trace_ids)}

        stored = [row["embedding"] for row in rows]
        encoder = None
        if self.encoder_factory is not None:
            try:
                encoder = self.encoder_factory()
            except Exception as error:  # noqa: BLE001 - fall back, never fail to start
                self.encoder_error = str(error)
                encoder = None
        self.index = SemanticIndex.fit(
            self.documents,
            use_bge=self.use_bge,
            bge_model=self.bge_model or "BAAI/bge-small-en-v1.5",
            bge_revision=self.bge_revision or None,
            encoder=encoder,
        )
        if stored and all(blob for blob in stored):
            candidate = np.stack([np.frombuffer(blob, dtype=np.float32) for blob in stored])
            # A different backend produces different dimensions; reuse only what
            # this index could have written.
            if candidate.shape[1] == self.index.dims:
                self.vectors = candidate
            else:
                self.vectors = self._encode_and_store()
        else:
            self.vectors = self._encode_and_store()
        self.user_vectors = self.index.encode(self.user_documents)
        self.catalog = DatasetCatalog.from_db(self.db)

    def _encode_and_store(self) -> np.ndarray:
        assert self.index is not None
        vectors = self.index.encode(self.documents)
        self.db.execute_many(
            "UPDATE traces SET embedding=? WHERE id=?",
            [(np.asarray(vector, dtype=np.float32).tobytes(), trace_id) for vector, trace_id in zip(vectors, self.trace_ids, strict=True)],
        )
        return vectors

    # -- lookups ----------------------------------------------------------
    def rows_for(self, trace_ids: Sequence[str]) -> list[int]:
        return [self._position[trace_id] for trace_id in trace_ids if trace_id in self._position]

    def vectors_for(self, trace_ids: Sequence[str]) -> np.ndarray:
        rows = self.rows_for(trace_ids)
        if not rows:
            return np.zeros((0, self.vectors.shape[1] if self.vectors.size else 1), dtype=np.float32)
        return self.vectors[rows]

    def documents_for(self, trace_ids: Sequence[str]) -> list[str]:
        return [self.documents[row] for row in self.rows_for(trace_ids)]

    def user_documents_for(self, trace_ids: Sequence[str]) -> list[str]:
        return [self.user_documents[row] for row in self.rows_for(trace_ids)]

    def user_vectors_for(self, trace_ids: Sequence[str]) -> np.ndarray:
        rows = self.rows_for(trace_ids)
        if not rows:
            return np.zeros((0, self.user_vectors.shape[1] if self.user_vectors.size else 1), dtype=np.float32)
        return self.user_vectors[rows]

    def intent_vectors_for(self, trace_ids: Sequence[str]) -> np.ndarray:
        """Sparse TF-IDF over ask-plus-tools, built on demand for one answer.

        These are wide (one column per vocabulary term) so they are not cached
        for the whole dataset; an answer only ever groups its own eligible set.
        """
        self.ensure()
        assert self.index is not None
        rows = self.rows_for(trace_ids)
        return self.index.lexical_matrix([self.intent_documents[row] for row in rows])

    def encode_query(self, text: str) -> np.ndarray:
        self.ensure()
        assert self.index is not None
        return self.index.encode_one(text)

    def rank(self, question: str, trace_ids: Sequence[str]) -> list[tuple[str, float]]:
        """Cosine similarity of every candidate trace to the question."""
        self.ensure()
        vectors = self.vectors_for(trace_ids)
        if not vectors.size:
            return []
        query = self.encode_query(question)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        normalized = vectors / np.clip(norms, 1e-8, None)
        query_norm = float(np.linalg.norm(query)) or 1.0
        scores = normalized @ (query / query_norm)
        present = [trace_id for trace_id in trace_ids if trace_id in self._position]
        pairs = list(zip(present, (float(value) for value in scores)))
        pairs.sort(key=lambda item: item[1], reverse=True)
        return pairs

    @property
    def backend(self) -> str:
        return self.index.backend if self.index else "unbuilt"
