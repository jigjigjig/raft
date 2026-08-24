"""Local aspect judge.

Layer 3 asks a typed question of every eligible conversation. With Otari
configured a model answers it. Without credentials Raft still has to answer
honestly, so this module actually evaluates the question against each trace
rather than reading a pre-baked column: it scopes the evidence to whoever the
question is about, expands the question's terms using the corpus's own latent
space, scores every trace, and picks the decision boundary from the shape of the
score distribution.

It is a classifier, not a language model, and every surface that shows its
output labels it `local:semantic-judge-v1`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from raft.embedding import SemanticIndex, tokenize


JUDGE_ID = "local:semantic-judge-v1"

# Tuned on the labelled evaluation in tests/test_judge.py; changing either value
# should be accompanied by re-running it.
_YES_FRACTION_OF_PEAK = 0.20
_RARE_TERM_POWER = 2.4
_DENSE_WEIGHT = 0.2
_ABSOLUTE_FLOOR = 0.02
_YES_Z_SCORE = 2.0

_ASSISTANT_SUBJECT = re.compile(r"\b(?:the )?(?:assistant|agent|bot|model|llm|it|raft)\b", re.IGNORECASE)
_USER_SUBJECT = re.compile(r"\b(?:the )?(?:user|customer|person|people|they|caller|client)\b", re.IGNORECASE)
_NEGATION = re.compile(r"\b(?:not|never|fail(?:s|ed)? to|without|neither|no)\b", re.IGNORECASE)

# Verbs that frame a question rather than name its subject. "Did the user *ask*
# about shipping" is about shipping; every conversation involves asking.
_FRAMING_VERBS = frozenset(
    {"ask", "give", "get", "got", "mention", "say", "tell", "receive", "provide", "want",
     "need", "use", "do", "make", "happen", "involve", "report", "request", "answer"}
)

_QUESTION_LEAD = re.compile(
    r"^\s*(?:did|do|does|is|are|was|were|has|have|can|could|should|would|will)\s+"
    r"(?:the\s+)?(?:assistant|agent|bot|model|user|customer|person|people|conversation|this conversation|it|they)?\s*",
    re.IGNORECASE,
)


@dataclass
class Verdict:
    value: bool
    confidence: float
    score: float
    quote: str
    quote_span_index: int | None
    evidence_terms: list[str]


@dataclass
class AspectPlan:
    """Everything the judge derived from the aspect question, shown in the UI."""

    scope: str  # assistant | user | conversation
    negated: bool
    terms: list[str]
    expanded_terms: list[str]
    threshold: float
    separation: float
    method: str


class SemanticAspectJudge:
    def __init__(self, index: SemanticIndex):
        self.index = index

    # -- question analysis ------------------------------------------------
    def analyse(self, question: str) -> tuple[AspectPlan, np.ndarray, dict[int, float]]:
        # Whichever party is named first is the actor: in "did the assistant
        # give the user a product suggestion" the evidence lives in the
        # assistant's turns, not the user's.
        assistant_at = _ASSISTANT_SUBJECT.search(question)
        user_at = _USER_SUBJECT.search(question)
        if assistant_at and (not user_at or assistant_at.start() < user_at.start()):
            scope = "assistant"
        elif user_at:
            scope = "user"
        else:
            scope = "conversation"

        core = _QUESTION_LEAD.sub("", question.strip().rstrip("?")).strip()
        negated = bool(_NEGATION.search(core))
        core = _NEGATION.sub(" ", core)

        weights = self.index.lexical(core)
        weights = self._sharpen(weights)
        weights.update(self._acronyms(core))
        terms = [self.index.reverse_vocabulary[position] for position in weights]
        # The gate is built from the question's own words. Latent-space
        # neighbours added below are useful for ranking but must not decide
        # which conversations are eligible at all.
        self.last_gate = self.gate_terms(weights)
        expanded = self._expand(weights)
        vector = self.index.encode_one(core)
        plan = AspectPlan(
            scope=scope,
            negated=negated,
            terms=[term.replace("_", " ") for term in terms][:10],
            expanded_terms=[self.index.reverse_vocabulary[position].replace("_", " ") for position in expanded if position not in weights][:8],
            threshold=0.0,
            separation=0.0,
            method="cosine + expanded lexical overlap",
        )
        return plan, vector, expanded

    @staticmethod
    def _sharpen(weights: dict[int, float], power: float = _RARE_TERM_POWER) -> dict[int, float]:
        """Lean on the rarest words in the question.

        "Suggest a specific product" and "the product page" share `product`; only
        `suggest` separates them. TF-IDF already ranks `suggest` higher, but not
        by enough to stop a common term from carrying a match on its own.
        """
        if not weights:
            return weights
        sharpened = {index: value**power for index, value in weights.items()}
        norm = math.sqrt(sum(value * value for value in sharpened.values())) or 1.0
        return {index: value / norm for index, value in sharpened.items()}

    def _acronyms(self, text: str) -> dict[int, float]:
        """Link "single sign-on" to a corpus that only ever writes "SSO"."""
        words = [word for word in re.split(r"[^a-z0-9]+", text.casefold()) if word]
        found: dict[int, float] = {}
        for size in (2, 3, 4):
            for start in range(len(words) - size + 1):
                window = words[start : start + size]
                if any(len(word) < 2 for word in window):
                    continue
                acronym = "".join(word[0] for word in window)
                position = self.index.vocabulary.get(acronym)
                if position is not None and float(self.index.idf[position]) > 2.0:
                    found[position] = max(found.get(position, 0.0), 0.75)
        return found

    def _expand(self, weights: dict[int, float], neighbours: int = 3) -> dict[int, float]:
        """Add corpus-adjacent terms so paraphrases of the question still match."""
        expanded = dict(weights)
        projection = self.index.projection
        if projection.size == 0 or not weights:
            return expanded
        norms = np.linalg.norm(projection, axis=1, keepdims=True)
        term_vectors = projection / np.clip(norms, 1e-8, None)
        for position, weight in list(weights.items()):
            similarity = term_vectors @ term_vectors[position]
            similarity[position] = -2.0
            top = np.argpartition(similarity, -neighbours)[-neighbours:]
            for candidate in top:
                score = float(similarity[candidate])
                if score < 0.68:
                    continue
                expanded[int(candidate)] = max(expanded.get(int(candidate), 0.0), weight * score * 0.6)
        return expanded

    # -- scoring ----------------------------------------------------------
    def score_documents(
        self,
        vector: np.ndarray,
        expanded: dict[int, float],
        documents: Sequence[str],
        document_vectors: np.ndarray,
        context: Sequence[str] | None = None,
        context_weight: float = 0.35,
        gate: set[int] | None = None,
    ) -> np.ndarray:
        """Score the scoped evidence, with the rest of the conversation as context.

        "Did the assistant suggest a product?" is answered by the assistant's
        turns, but the user's "which jacket would you recommend" is what makes
        that reading obvious, so context is scored too and mixed in below full
        weight rather than discarded.
        """
        primary = self._score(vector, expanded, documents, document_vectors, gate)
        if not context:
            return primary
        secondary = self._score(vector, expanded, context, None, gate)
        return (1.0 - context_weight) * primary + context_weight * secondary

    def gate_terms(self, weights: dict[int, float], tolerance: float = 0.6, floor: float = 3.0) -> set[int]:
        """The words a conversation must actually contain to be a candidate.

        Without this, "did the assistant suggest a product" scores a refusal that
        merely says "I am not able to give advice" highly, because it shares the
        question's filler verbs and nothing forces the discriminating noun to be
        present. The gate is the question's rarest terms.
        """
        if not weights:
            return set()
        scores = {
            index: float(self.index.idf[index])
            for index in weights
            if "_" not in self.index.reverse_vocabulary[index]
            and self.index.reverse_vocabulary[index] not in _FRAMING_VERBS
        }
        if not scores:
            return set()
        ceiling = max(scores.values())
        bar = max(floor, tolerance * ceiling)
        gate = {index for index, value in scores.items() if value >= bar}
        if not gate:
            gate = {max(scores, key=lambda index: scores[index])}
        return gate

    def _score(
        self,
        vector: np.ndarray,
        expanded: dict[int, float],
        documents: Sequence[str],
        document_vectors: np.ndarray | None,
        gate: set[int] | None = None,
    ) -> np.ndarray:
        dense = np.zeros(len(documents), dtype=np.float32)
        if document_vectors is not None and document_vectors.size:
            norms = np.linalg.norm(document_vectors, axis=1, keepdims=True)
            normalized = document_vectors / np.clip(norms, 1e-8, None)
            query_norm = float(np.linalg.norm(vector)) or 1.0
            dense = normalized @ (vector / query_norm)

        lexical = np.zeros(len(documents), dtype=np.float32)
        query_norm = math.sqrt(sum(value * value for value in expanded.values())) or 1.0
        for position, document in enumerate(documents):
            weights = self.index.lexical(document)
            if not weights:
                continue
            if gate and not (gate & weights.keys()):
                continue  # the question's own subject is absent from this conversation
            overlap = sum(value * expanded.get(index, 0.0) for index, value in weights.items())
            lexical[position] = overlap / query_norm
        # Lexical overlap is what actually decides a specific proposition. The
        # latent-space term only softens the edge for paraphrases, so it is
        # deliberately the minority of the score - given more weight it starts
        # matching whatever a document is broadly *about*.
        return (1.0 - _DENSE_WEIGHT) * lexical + _DENSE_WEIGHT * np.clip(dense, 0.0, None)

    def relevance_feedback(
        self,
        scores: np.ndarray,
        documents: Sequence[str],
        expanded: dict[int, float],
        *,
        top: int | None = None,
        beta: float = 0.35,
        terms: int = 12,
    ) -> tuple[dict[int, float], list[str], set[int]]:
        """Rocchio expansion: learn the question's vocabulary from its own best hits.

        A question phrased as "shipping or delivery" will not lexically match a
        conversation that says "my parcel has not arrived". The highest-scoring
        conversations do use the corpus's words for the idea, so their shared
        terms are folded back into the query.

        The danger is drift: one strong-but-wrong hit can drag the query onto its
        own topic. Two guards prevent that - a term must recur across several of
        the feedback documents to be added at all, and the total weight the
        expansion may contribute is capped below the original question's.
        """
        if len(scores) < 20 or not expanded:
            return expanded, [], set()
        top = top or max(6, int(len(scores) * 0.02))
        order = np.argsort(scores)[::-1][:top]
        peak = float(scores[order[0]]) if len(order) else 0.0
        if peak <= 1e-9:
            return expanded, [], set()
        feedback = [int(position) for position in order if float(scores[position]) >= 0.4 * peak]
        if len(feedback) < 3:
            return expanded, [], set()

        mass: dict[int, float] = {}
        document_frequency: dict[int, int] = {}
        for position in feedback:
            for index, value in self.index.lexical(documents[position]).items():
                mass[index] = mass.get(index, 0.0) + value
                document_frequency[index] = document_frequency.get(index, 0) + 1
        # A term seen in one document is that document's topic, not the question's.
        quorum = max(2, len(feedback) // 3)
        candidates = [
            (value, index)
            for index, value in mass.items()
            if index not in expanded and document_frequency[index] >= quorum
        ]
        if not candidates:
            return expanded, [], set()
        candidates.sort(reverse=True)
        chosen = candidates[:terms]
        original_mass = sum(expanded.values())
        scale = sum(value for value, _ in chosen) or 1.0
        budget = beta * original_mass

        merged = dict(expanded)
        added: list[str] = []
        indices: set[int] = set()
        for value, index in chosen:
            merged[index] = budget * value / scale
            added.append(self.index.reverse_vocabulary[index].replace("_", " "))
            indices.add(index)
        return merged, added, indices

    def threshold(self, scores: np.ndarray) -> tuple[float, float, str]:
        """Cut the ranking at a fixed fraction of its robust peak.

        Aspect answers are lopsided - a few dozen yes out of eight hundred - and
        the score curve decays smoothly rather than in one clean step, so
        variance splits and knee-finding both land in the wrong place. What does
        hold across questions is scale: a conversation scoring far below the
        clearest matches is not about the question. The peak is the mean of the
        top three so one outlier cannot move the boundary.
        """
        if not len(scores):
            return float("inf"), 0.0, "no_rows"
        ordered = np.sort(scores)[::-1]
        peak = float(np.mean(ordered[: min(3, len(ordered))]))
        if peak <= 1e-6:
            return float("inf"), 0.0, "no_signal"
        # Two bars, whichever is higher: a fraction of the peak (handles a
        # question with a clear winner) and a z-score over the whole
        # distribution (handles a question the corpus has no words for, where
        # everything scores about the same and nothing should come back yes).
        cut = max(
            _YES_FRACTION_OF_PEAK * peak,
            _ABSOLUTE_FLOOR,
            float(np.mean(scores) + _YES_Z_SCORE * np.std(scores)),
        )
        # A question that matches most of the corpus has not discriminated
        # anything; keep only the clearest half rather than answering "yes" to
        # everything.
        if float(np.mean(ordered >= cut)) > 0.5:
            cut = max(cut, float(ordered[len(ordered) // 2]))
        median = float(np.median(ordered))
        separation = min(1.0, max(0.0, (peak - median) / peak))
        return cut, separation, f"max({_YES_FRACTION_OF_PEAK:.2f}×peak, mean+{_YES_Z_SCORE:g}σ)"

    def verdict(
        self,
        score: float,
        threshold: float,
        spread: float,
        *,
        negated: bool,
        scoped_text: str,
        expanded: dict[int, float],
    ) -> Verdict:
        value = score >= threshold
        if negated:
            value = not value
        margin = abs(score - threshold) / (spread or 1.0)
        confidence = round(min(0.97, 0.5 + 0.47 * min(1.0, margin * 2.6)), 3)
        quote, terms = self.best_sentence(scoped_text, expanded)
        return Verdict(
            value=bool(value),
            confidence=confidence,
            score=round(float(score), 6),
            quote=quote,
            quote_span_index=None,
            evidence_terms=terms,
        )

    def best_sentence(self, text: str, expanded: dict[int, float]) -> tuple[str, list[str]]:
        sentences = [part.strip() for part in re.split(r"(?<=[.?!])\s+|\n+", text) if len(part.strip()) > 12]
        if not sentences:
            return text.strip()[:200], []
        best, best_score, best_terms = sentences[0], -1.0, []
        for sentence in sentences:
            weights = self.index.lexical(sentence)
            score = sum(value * expanded.get(index, 0.0) for index, value in weights.items())
            if score > best_score:
                matched = sorted(
                    (
                        self.index.reverse_vocabulary[index].replace("_", " ")
                        for index in weights
                        if expanded.get(index, 0.0) > 0
                    ),
                    key=len,
                    reverse=True,
                )[:4]
                best, best_score, best_terms = sentence, score, matched
        return best, best_terms


def scope_text(spans: Sequence[dict], scope: str) -> str:
    """Restrict the evidence to whoever the aspect question is about."""
    if scope == "assistant":
        wanted = {"assistant_message", "tool_call", "tool_result"}
    elif scope == "user":
        wanted = {"user_message"}
    else:
        wanted = {"user_message", "assistant_message", "tool_call", "tool_result", "error"}
    parts = [span["content_redacted"] for span in spans if span["type"] in wanted]
    return "\n".join(parts) if parts else "\n".join(span["content_redacted"] for span in spans)
