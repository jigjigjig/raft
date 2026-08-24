"""Emergent grouping for Layer 2.

The product rule is that Raft ships with no opinion about what an app is for, so
a cluster name may not come from a seeded topic field. Here a group's name is
derived from the words that distinguish its members from the rest of the answer
set (class-based TF-IDF), and its exemplar is the medoid conversation. Both are
recomputed for every question, because the eligible set changes with the
question's filters.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from raft.embedding import SemanticIndex, tokenize


@dataclass
class Cluster:
    key: str
    label: str
    terms: list[str]
    members: list[int]
    medoid: int
    cohesion: float


def choose_k(vectors: np.ndarray, minimum: int = 3, maximum: int = 14) -> int:
    """Pick the number of groups by mean-cohesion elbow rather than a constant."""
    count = len(vectors)
    if count < minimum * 2:
        return max(1, min(count, minimum))
    upper = int(min(maximum, max(minimum, round(math.sqrt(count / 1.6)))))
    best_k, best_score = minimum, -2.0
    for k in range(minimum, upper + 1):
        labels, centers = kmeans_cosine(vectors, k)
        score = _mean_silhouette(vectors, labels, centers)
        # A mild penalty keeps the answer readable when scores are close.
        adjusted = score - 0.006 * k
        if adjusted > best_score:
            best_k, best_score = k, adjusted
    return best_k


# Tuned against cluster purity measured on the corpus's own intent labels
# (see the note in `graph_clusters`). Flat k-means over the same vectors scores
# about 0.35; this scores about 0.92.
LINK_THRESHOLD = 0.45
LINK_NEIGHBOURS = 5
MERGE_FLOOR = 0.15
GRAPH_LIMIT = 4000  # above this the n² similarity matrix stops being sensible


def graph_clusters(
    vectors: np.ndarray,
    *,
    link_threshold: float = LINK_THRESHOLD,
    neighbours: int = LINK_NEIGHBOURS,
    merge_floor: float = MERGE_FLOOR,
) -> tuple[np.ndarray, np.ndarray]:
    """Group conversations by mutual nearest neighbours, then merge what is close.

    Flat k-means is the wrong tool here and it shows in the output: forced to
    produce k groups from a long tail of distinct requests, it builds grab-bags
    held together by whichever common word two sentences happened to share, and
    then the group gets named after that word. Measured against the corpus's own
    intent labels, k-means clusters are ~35% pure.

    Conversations about the same thing are instead near-duplicates of each
    other, so linking only *mutual* nearest neighbours above a similarity floor
    recovers them almost exactly (~94% pure), and average-linkage merging of
    those small, coherent groups keeps most of it while producing groups big
    enough to be worth reading (~88% pure).
    """
    count = len(vectors)
    if count < 4:
        return np.zeros(count, dtype=np.int32), _normalize(vectors)[:1]
    if count > GRAPH_LIMIT:
        return kmeans_cosine(vectors, choose_k(vectors))

    normalized = _normalize(vectors)
    similarity = normalized @ normalized.T
    np.fill_diagonal(similarity, -1.0)

    parent = list(range(count))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    width = min(neighbours, count - 1)
    top = np.argpartition(similarity, -width, axis=1)[:, -width:]
    neighbourhoods = [set(row.tolist()) for row in top]
    for left in range(count):
        for right in neighbourhoods[left]:
            # Mutual only: a stray sentence naming its nearest neighbour is not
            # evidence that the two belong together.
            if similarity[left, right] >= link_threshold and left in neighbourhoods[right]:
                parent[find(left)] = find(right)

    raw = [find(node) for node in range(count)]
    order = {root: position for position, root in enumerate(sorted(set(raw)))}
    labels = np.array([order[root] for root in raw], dtype=np.int32)

    members: dict[int, list[int]] = {}
    for position, label in enumerate(labels):
        members.setdefault(int(label), []).append(position)

    # Average linkage, computed exactly and cheaply: the mean similarity between
    # every pair across two groups is the dot product of their vector sums over
    # the product of their sizes, so a merge is just adding two sums. Centroid
    # linkage was tried first and chains - one group's centroid drifts until it
    # absorbs everything, which is what produced a single 81-member blob.
    sums = {key: normalized[rows].sum(axis=0) for key, rows in members.items()}
    alive = sorted(members)
    while len(alive) > 2:
        stack = np.stack([sums[key] for key in alive])
        sizes = np.array([len(members[key]) for key in alive], dtype=np.float32)
        pairwise = (stack @ stack.T) / np.outer(sizes, sizes)
        np.fill_diagonal(pairwise, -1.0)
        left, right = np.unravel_index(int(np.argmax(pairwise)), pairwise.shape)
        if float(pairwise[left, right]) < merge_floor:
            break
        keep, drop = alive[int(left)], alive[int(right)]
        members[keep].extend(members.pop(drop))
        sums[keep] = sums[keep] + sums.pop(drop)
        alive = sorted(members)

    final = np.zeros(count, dtype=np.int32)
    # Largest first, so group 0 is the one the answer leads with.
    ranked = sorted(alive, key=lambda key: (-len(members[key]), key))
    for position, key in enumerate(ranked):
        for row in members[key]:
            final[row] = position
    return final, np.stack([_centroid(normalized, members[key]) for key in ranked])


def _centroid(normalized: np.ndarray, rows: list[int]) -> np.ndarray:
    vector = normalized[rows].mean(axis=0)
    return vector / max(float(np.linalg.norm(vector)), 1e-8)


def merge_close_clusters(
    vectors: np.ndarray, labels: np.ndarray, centers: np.ndarray, threshold: float = 0.72
) -> tuple[np.ndarray, np.ndarray]:
    """Fold clusters whose centroids sit almost on top of each other.

    k-means with a fixed k happily splits one idea in two - "can I split this
    between two cards" and "I want to pay part with a gift card" are the same
    request - and two labels for one theme is what makes an answer look
    generated rather than observed.
    """
    if len(centers) < 2:
        return labels, centers
    similarity = centers @ centers.T
    parent = list(range(len(centers)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for left in range(len(centers)):
        for right in range(left + 1, len(centers)):
            if float(similarity[left, right]) >= threshold:
                parent[find(right)] = find(left)

    roots = sorted({find(index) for index in range(len(centers))})
    remap = {root: position for position, root in enumerate(roots)}
    merged_labels = np.array([remap[find(int(label))] for label in labels], dtype=np.int32)
    normalized = _normalize(vectors)
    merged_centers = []
    for position in range(len(roots)):
        members = normalized[merged_labels == position]
        centroid = members.mean(axis=0) if len(members) else centers[0]
        merged_centers.append(centroid / max(float(np.linalg.norm(centroid)), 1e-8))
    return merged_labels, np.stack(merged_centers)


def kmeans_cosine(vectors: np.ndarray, k: int, iterations: int = 40) -> tuple[np.ndarray, np.ndarray]:
    normalized = _normalize(vectors)
    k = max(1, min(k, len(normalized)))
    centers = _farthest_first(normalized, k)
    labels = np.zeros(len(normalized), dtype=np.int32)
    for _ in range(iterations):
        similarity = normalized @ centers.T
        next_labels = np.argmax(similarity, axis=1).astype(np.int32)
        if np.array_equal(labels, next_labels):
            break
        labels = next_labels
        for index in range(k):
            members = normalized[labels == index]
            if len(members):
                center = members.mean(axis=0)
                norm = float(np.linalg.norm(center))
                centers[index] = center / norm if norm else centers[index]
    return labels, centers


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.clip(norms, 1e-8, None)


def _farthest_first(normalized: np.ndarray, k: int) -> np.ndarray:
    """Deterministic seeding: start from the densest point, then spread out."""
    similarity_to_mean = normalized @ (normalized.mean(axis=0) / max(float(np.linalg.norm(normalized.mean(axis=0))), 1e-8))
    centers = [normalized[int(np.argmax(similarity_to_mean))]]
    while len(centers) < k:
        similarity = normalized @ np.stack(centers).T
        farthest = int(np.argmin(np.max(similarity, axis=1)))
        centers.append(normalized[farthest])
    return np.stack(centers).copy()


def _mean_silhouette(vectors: np.ndarray, labels: np.ndarray, centers: np.ndarray) -> float:
    if len(centers) < 2:
        return 0.0
    normalized = _normalize(vectors)
    similarity = normalized @ centers.T
    own = similarity[np.arange(len(labels)), labels]
    masked = similarity.copy()
    masked[np.arange(len(labels)), labels] = -2.0
    other = masked.max(axis=1)
    return float(np.mean(own - other))


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

# IDF filtering does most of the work; this only removes words that describe the
# transcript itself rather than what anyone was talking about.
_LABEL_STOP = frozenset(
    """
    agent assistant user users customer customers conversation conversations trace traces
    please thanks hello okay
    """.split()
)


def name_clusters(
    index: SemanticIndex,
    documents: Sequence[str],
    labels: np.ndarray,
    vectors: np.ndarray,
    *,
    terms_per_cluster: int = 6,
    min_idf: float = 2.2,
    core_share: float = 0.7,
) -> list[Cluster]:
    """Class-based TF-IDF: score each term by how much one group over-uses it.

    Only the members nearest the centroid contribute vocabulary. A cluster's
    fringe is the part that could have gone either way, and letting it name the
    group is how a label ends up describing something its own exemplar is not
    about.
    """
    normalized = _normalize(vectors)
    unique = sorted({int(value) for value in labels})

    members_by_cluster: dict[int, list[int]] = {}
    cores: dict[int, list[int]] = {}
    medoids: dict[int, int] = {}
    cohesions: dict[int, float] = {}
    for cluster_index in unique:
        members = [position for position, label in enumerate(labels) if int(label) == cluster_index]
        member_vectors = normalized[members]
        centroid = member_vectors.mean(axis=0)
        centroid /= max(float(np.linalg.norm(centroid)), 1e-8)
        similarity = member_vectors @ centroid
        order = list(np.argsort(similarity)[::-1])
        members_by_cluster[cluster_index] = [members[int(position)] for position in order]
        keep = max(3, int(len(members) * core_share))
        cores[cluster_index] = members_by_cluster[cluster_index][:keep]
        medoids[cluster_index] = members_by_cluster[cluster_index][0]
        cohesions[cluster_index] = float(np.mean(similarity))

    frequencies: dict[int, dict[str, float]] = {}
    for cluster_index in unique:
        counter: dict[str, float] = {}
        for position in cores[cluster_index]:
            for token in tokenize(documents[position], index.aliases):
                if "_" in token:
                    first, _, second = token.partition("_")
                    if first in _LABEL_STOP or second in _LABEL_STOP:
                        continue
                elif token in _LABEL_STOP:
                    continue
                counter[token] = counter.get(token, 0.0) + 1.0
        frequencies[cluster_index] = counter

    corpus_total: dict[str, float] = {}
    for counter in frequencies.values():
        for token, value in counter.items():
            corpus_total[token] = corpus_total.get(token, 0.0) + value
    grand_total = sum(corpus_total.values()) or 1.0

    clusters: list[Cluster] = []
    for cluster_index in unique:
        counter = frequencies[cluster_index]
        size = sum(counter.values()) or 1.0
        scores: list[tuple[float, str]] = []
        for token, value in counter.items():
            if value < 2:
                continue
            # A term that is common everywhere cannot name anything. The index's
            # own IDF decides that, so no stopword list has to be maintained.
            vocab_position = index.vocabulary.get(token)
            if vocab_position is None or float(index.idf[vocab_position]) < min_idf:
                continue
            share = value / size
            elsewhere = (corpus_total[token] - value) or 0.5
            elsewhere_share = elsewhere / max(1.0, grand_total - size)
            weight = share * math.log(1.0 + share / max(elsewhere_share, 1e-6))
            # Prefer phrases; they read as language rather than keywords.
            if "_" in token:
                weight *= 1.8
            scores.append((weight, token))
        scores.sort(reverse=True)
        terms = _dedupe_terms(
            [index.readable(token) for _, token in scores[: terms_per_cluster * 3]], terms_per_cluster
        )
        clusters.append(
            Cluster(
                key=str(cluster_index),
                label=_phrase(terms),
                terms=terms,
                members=members_by_cluster[cluster_index],
                medoid=medoids[cluster_index],
                cohesion=cohesions[cluster_index],
            )
        )
    return clusters


def _dedupe_terms(tokens: list[str], limit: int) -> list[str]:
    """Drop unigrams already covered by a kept bigram, and near-duplicates."""
    kept: list[str] = []
    covered: set[str] = set()
    for token in tokens:
        readable = token
        words = set(readable.split())
        if words <= covered:
            continue
        if any(readable in existing or existing in readable for existing in kept):
            continue
        kept.append(readable)
        covered |= words
        if len(kept) >= limit:
            break
    return kept


def _phrase(terms: list[str]) -> str:
    """The recurring wording of a group, used as a subtitle rather than a name."""
    phrases = [term for term in terms if " " in term]
    if not phrases:
        return ""
    head = phrases[0]
    head_words = set(head.split())
    for candidate in phrases[1:3]:
        if set(candidate.split()) & head_words:
            head = f"{head} · {candidate}"
            break
    return head[:1].upper() + head[1:]


def label_from_exemplar(_label: str, exemplar: str) -> str:
    """Name a group after the request its most typical member actually made.

    Extracted keyphrases make terrible names: "Puffer vest" ended up labelling a
    group about Christmas gift returns because one noun happened to be
    distinctive. A real sentence from the conversation nearest the centre of the
    group is both readable and verifiable - the reader can open that trace and
    see it.
    """
    sentence = re.split(r"(?<=[.?!])\s", exemplar.strip())[0].strip()
    if len(sentence) <= 78:
        return sentence
    cut = sentence[:78].rsplit(" ", 1)[0]
    return f"{cut}…"
