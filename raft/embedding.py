"""Local semantic index.

Hosted Otari does not mount an embeddings route, so Layer 2 has to embed
locally. The default is a corpus-fitted TF-IDF + truncated-SVD (latent semantic)
index: it needs no download, it is deterministic, and - unlike the hashed
bag-of-words vectors this replaces - it actually places related wordings near
each other, which is what makes clustering and free-text question matching mean
anything.

`RAFT_USE_BGE=1` swaps the dense half for the pinned `BAAI/bge-small-en-v1.5`
sentence-transformer when it is installed. The lexical half is kept either way
because the aspect judge and the search box both need exact-term evidence, not
only vector similarity.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field as dataclass_field
from typing import Iterable, Sequence

import numpy as np


TOKEN_RE = re.compile(r"[a-z][a-z0-9_\-]*(?:'[a-z]+)?")

STOPWORDS = frozenset(
    """
    a about above after again against all am an and any are aren as at be because been before being
    below between both but by can cannot could couldn did didn do does doesn doing don down during
    each few for from further had hadn has hasn have haven having he her here hers herself him himself
    his how i if in into is isn it its itself just ll me more most my myself no nor not now of off on
    once only or other our ours ourselves out over own re s same shan she should shouldn so some such
    t than that the their theirs them themselves then there these they this those through to too under
    until up ve very was wasn we were weren what when where which while who whom why will with won
    would wouldn you your yours yourself yourselves
    """.split()
)

# Words that carry product meaning even though they are short or common.
KEEP = frozenset({"not", "no", "cannot", "can", "want", "need", "why", "how", "when", "where", "cost"})


# Inflectional stemming only. Users write "shipped", "shipping" and "ships" for
# the same idea, so those forms must collide - but reducing "delivery" to
# "deliv" by rule would collide with words that mean something else. Derivational
# forms are handled by the corpus-verified alias table below instead, which only
# merges two words when both actually appear in this dataset.
_DOUBLE_KEEP = frozenset("sl")
_KEEP_WHOLE = frozenset(
    {"as", "is", "its", "us", "this", "less", "gas", "bus", "yes", "was", "has", "does",
     "series", "status", "always", "address", "process", "access", "business", "analysis"}
)


def stem(word: str) -> str:
    """Fold plural and tense inflections onto one form."""
    if len(word) <= 3 or word in _KEEP_WHOLE:
        return word
    root = word
    if root.endswith("ies") and len(root) > 4:
        root = root[:-3] + "y"
    elif root.endswith("sses") or root.endswith("shes") or root.endswith("ches"):
        root = root[:-2]
    elif root.endswith("s") and not root.endswith("ss"):
        root = root[:-1]
    if root.endswith("ing") and len(root) > 5:
        root = root[:-3]
    elif root.endswith("ied") and len(root) > 4:
        root = root[:-3] + "y"
    elif root.endswith("ed") and len(root) > 4:
        root = root[:-2]
    else:
        return root
    # "shipping" -> "shipp" -> "ship"; "call" and "pass" keep their pair.
    if len(root) > 3 and root[-1] == root[-2] and root[-1] not in _DOUBLE_KEEP:
        root = root[:-1]
    return root or word


# Nominalising suffixes, tried longest-first. A reduction is only accepted when
# the reduced form is itself a word in the corpus.
_DERIVATIONS = (
    ("ations", ""), ("ation", ""), ("ications", "y"), ("ication", "y"),
    ("ments", ""), ("ment", ""), ("ances", ""), ("ance", ""), ("ences", ""), ("ence", ""),
    ("ions", ""), ("ion", ""), ("ives", "e"), ("ive", "e"), ("als", ""), ("al", ""),
    ("ities", "e"), ("ity", "e"), ("eries", "er"), ("ery", "er"), ("ness", ""),
    ("ers", ""), ("er", ""), ("ors", ""), ("or", ""),
)


def build_aliases(counts: dict[str, int]) -> dict[str, str]:
    """Merge "cancellation" into "cancel" only when both are in this corpus."""
    aliases: dict[str, str] = {}
    for token in counts:
        if "_" in token or len(token) < 5:
            continue
        for suffix, replacement in _DERIVATIONS:
            if not token.endswith(suffix):
                continue
            base = token[: -len(suffix)] + replacement
            for candidate in (base, base.rstrip("l") if base.endswith("ll") else base, f"{base}e"):
                if len(candidate) >= 3 and candidate in counts and candidate != token:
                    aliases[token] = candidate
                    break
            if token in aliases:
                break
    # Collapse chains so "cancellations" and "cancellation" reach "cancel".
    for token in list(aliases):
        seen = {token}
        target = aliases[token]
        while target in aliases and target not in seen:
            seen.add(target)
            target = aliases[target]
        aliases[token] = target
    return aliases


_SENTENCE_SPLIT = re.compile(r"[.?!;\n]+")


def tokenize(text: str, aliases: dict[str, str] | None = None) -> list[str]:
    """Unigrams plus within-sentence bigrams.

    Bigrams are built per sentence on purpose: "…two days later. What am I
    owed?" would otherwise produce the phrase "late owed", which names nothing
    and reads as nonsense when it becomes a cluster label.
    """
    words: list[str] = []
    bigrams: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text):
        tokens = [
            stem(word.rstrip("'"))
            for word in TOKEN_RE.findall(sentence.casefold())
            if word in KEEP or word not in STOPWORDS
        ]
        if aliases:
            tokens = [aliases.get(token, token) for token in tokens]
        words.extend(tokens)
        bigrams.extend(f"{first}_{second}" for first, second in zip(tokens, tokens[1:]))
    return words + bigrams


@dataclass
class SemanticIndex:
    """TF-IDF vocabulary plus an optional dense projection."""

    vocabulary: dict[str, int]
    idf: np.ndarray
    projection: np.ndarray  # (vocab, dims)
    dims: int
    backend: str
    document_count: int
    aliases: dict[str, str] = dataclass_field(default_factory=dict)
    surface_forms: dict[str, str] = dataclass_field(default_factory=dict)
    _encoder: object | None = None

    # -- construction -----------------------------------------------------
    @classmethod
    def fit(
        cls,
        documents: Sequence[str],
        *,
        dims: int = 192,
        min_document_frequency: int = 2,
        max_document_ratio: float = 0.55,
        use_bge: bool = False,
        bge_model: str = "BAAI/bge-small-en-v1.5",
        bge_revision: str | None = None,
        encoder: object | None = None,
        seed: int = 20260821,
    ) -> "SemanticIndex":
        documents = list(documents) or [""]
        # First pass finds the surface forms; the alias table is built from them
        # and the corpus is then re-tokenised through it.
        surface: dict[str, int] = {}
        forms: dict[str, dict[str, int]] = {}
        for document in documents:
            for token in set(tokenize(document)):
                surface[token] = surface.get(token, 0) + 1
            # Remember how people actually spell each stem, so a label can read
            # "running shoes" instead of the index's internal "run shoe".
            for word in TOKEN_RE.findall(document.casefold()):
                if word in KEEP or word not in STOPWORDS:
                    bucket = forms.setdefault(stem(word.rstrip("'")), {})
                    bucket[word] = bucket.get(word, 0) + 1
        aliases = build_aliases(surface)
        surface_forms = {
            token: max(counts.items(), key=lambda item: item[1])[0] for token, counts in forms.items()
        }

        counts: dict[str, int] = {}
        tokenized: list[list[str]] = []
        for document in documents:
            tokens = tokenize(document, aliases)
            tokenized.append(tokens)
            for token in set(tokens):
                counts[token] = counts.get(token, 0) + 1

        total = len(documents)
        ceiling = max(2, int(total * max_document_ratio))
        floor = min_document_frequency if total > 20 else 1
        vocabulary = {
            token: index
            for index, token in enumerate(
                sorted(token for token, count in counts.items() if floor <= count <= ceiling)
            )
        }
        if not vocabulary:
            vocabulary = {token: index for index, token in enumerate(sorted(counts))}

        idf = np.zeros(len(vocabulary), dtype=np.float32)
        for token, index in vocabulary.items():
            idf[index] = math.log((1 + total) / (1 + counts[token])) + 1.0

        # A supplied encoder (the Otari embeddings client) wins; then the
        # optional local sentence-transformer; then the corpus-fitted fallback.
        backend = "tfidf_svd"
        if encoder is not None:
            backend = getattr(encoder, "name", "remote")
        elif use_bge:
            encoder = _load_encoder(bge_model, bge_revision)
            if encoder is not None:
                backend = f"bge:{bge_model}"

        if encoder is not None:
            probe = np.asarray(encoder.encode(["probe"], normalize_embeddings=True), dtype=np.float32)
            return cls(
                vocabulary=vocabulary,
                idf=idf,
                projection=np.zeros((len(vocabulary), 0), dtype=np.float32),
                dims=int(probe.shape[1]),
                backend=backend,
                document_count=total,
                aliases=aliases,
                surface_forms=surface_forms,
                _encoder=encoder,
            )

        matrix = _sparse_tfidf(tokenized, vocabulary, idf)
        target = int(min(dims, max(2, min(matrix.shape) - 1)))
        projection = _randomized_right_singular_vectors(matrix, target, seed=seed)
        return cls(
            vocabulary=vocabulary,
            idf=idf,
            projection=projection,
            dims=target,
            backend=backend,
            document_count=total,
            aliases=aliases,
            surface_forms=surface_forms,
        )

    # -- encoding ---------------------------------------------------------
    def lexical(self, text: str) -> dict[int, float]:
        """Sublinear TF-IDF weights, L2 normalised, keyed by vocabulary index."""
        raw: dict[int, float] = {}
        for token in tokenize(text, self.aliases):
            index = self.vocabulary.get(token)
            if index is not None:
                raw[index] = raw.get(index, 0.0) + 1.0
        if not raw:
            return {}
        weighted = {index: (1.0 + math.log(count)) * float(self.idf[index]) for index, count in raw.items()}
        norm = math.sqrt(sum(value * value for value in weighted.values())) or 1.0
        return {index: value / norm for index, value in weighted.items()}

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if self._encoder is not None:
            vectors = np.asarray(
                self._encoder.encode(list(texts), normalize_embeddings=True, batch_size=32),
                dtype=np.float32,
            )
            return vectors
        out = np.zeros((len(texts), self.dims), dtype=np.float32)
        for row, text in enumerate(texts):
            weights = self.lexical(text)
            if not weights:
                continue
            vector = np.zeros(self.dims, dtype=np.float32)
            for index, value in weights.items():
                vector += self.projection[index] * value
            norm = float(np.linalg.norm(vector))
            if norm:
                out[row] = vector / norm
        return out

    def lexical_matrix(self, texts: Sequence[str]) -> np.ndarray:
        """Full-vocabulary TF-IDF rows, L2 normalised.

        Grouping works markedly better on these than on the compressed vectors:
        the SVD projection is built to make loosely-related things near for
        retrieval, which is exactly the wrong property when the question is
        whether two conversations are about the same thing.
        """
        matrix = np.zeros((len(texts), len(self.vocabulary)), dtype=np.float32)
        for row, text in enumerate(texts):
            for index, value in self.lexical(text).items():
                matrix[row, index] = value
        return matrix

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]

    def readable(self, token: str) -> str:
        """Render an index token back into the words people actually wrote."""
        return " ".join(self.surface_forms.get(part, part) for part in token.split("_"))

    def terms_for(self, indices: Iterable[int]) -> list[str]:
        reverse = {index: token for token, index in self.vocabulary.items()}
        return [reverse[index] for index in indices if index in reverse]

    @property
    def reverse_vocabulary(self) -> dict[int, str]:
        if not hasattr(self, "_reverse"):
            object.__setattr__(self, "_reverse", {index: token for token, index in self.vocabulary.items()})
        return getattr(self, "_reverse")


def _sparse_tfidf(
    tokenized: Sequence[Sequence[str]],
    vocabulary: dict[str, int],
    idf: np.ndarray,
) -> np.ndarray:
    matrix = np.zeros((len(tokenized), len(vocabulary)), dtype=np.float32)
    for row, tokens in enumerate(tokenized):
        counts: dict[int, int] = {}
        for token in tokens:
            index = vocabulary.get(token)
            if index is not None:
                counts[index] = counts.get(index, 0) + 1
        if not counts:
            continue
        for index, count in counts.items():
            matrix[row, index] = (1.0 + math.log(count)) * idf[index]
        norm = float(np.linalg.norm(matrix[row]))
        if norm:
            matrix[row] /= norm
    return matrix


def _randomized_right_singular_vectors(matrix: np.ndarray, dims: int, *, seed: int) -> np.ndarray:
    """Top-`dims` right singular vectors via a randomized range finder."""
    rows, columns = matrix.shape
    dims = max(1, min(dims, columns))
    generator = np.random.default_rng(seed)
    oversample = min(columns, dims + 10)
    omega = generator.standard_normal((columns, oversample)).astype(np.float32)
    sample = matrix @ omega
    for _ in range(2):  # power iterations sharpen the spectrum
        sample = matrix @ (matrix.T @ sample)
    basis, _ = np.linalg.qr(sample)
    projected = basis.T @ matrix  # (oversample, columns)
    _, _, right = np.linalg.svd(projected, full_matrices=False)
    return np.ascontiguousarray(right[:dims].T.astype(np.float32))  # (vocab, dims)


def _load_encoder(model: str, revision: str | None):
    try:  # pragma: no cover - exercised only when the optional extra is installed
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return None
    try:
        return SentenceTransformer(model, revision=revision) if revision else SentenceTransformer(model)
    except Exception:  # pragma: no cover - offline or unavailable weights
        return None


def cosine_matrix(vectors: np.ndarray, query: np.ndarray) -> np.ndarray:
    if vectors.size == 0:
        return np.zeros(0, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    normalized = vectors / np.clip(norms, 1e-8, None)
    query_norm = float(np.linalg.norm(query)) or 1.0
    return normalized @ (query / query_norm)
