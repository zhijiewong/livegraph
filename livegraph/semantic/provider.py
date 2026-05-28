"""Embedding-provider protocol and the sentence-transformers implementation.

The sentence_transformers import is performed inside
``_import_sentence_transformers`` so the rest of livegraph can be imported
without the [semantic] extra installed. Tests monkeypatch that helper to
simulate both 'extra installed' and 'extra missing' states.
"""
from __future__ import annotations

from typing import Any, Protocol


class EmbeddingExtraMissing(Exception):
    """The [semantic] pip extra is not installed in this Python env."""


class EmbeddingDimensionMismatch(Exception):
    """Existing vector index has different dimensions than the new model."""

    def __init__(self, existing: int, new: int) -> None:
        super().__init__(
            f"Existing vector index uses {existing} dimensions; the "
            f"configured model produces {new}. Pass --rebuild to drop "
            f"the existing index and start over."
        )
        self.existing = existing
        self.new = new


class EmbeddingProvider(Protocol):
    """Minimal interface every embedding provider must satisfy."""

    name: str
    dimensions: int
    batch_size: int

    def encode(self, texts: list[str]) -> list[list[float]]: ...


def _import_sentence_transformers() -> Any:
    """Return the ``SentenceTransformer`` class, or raise on missing extra.

    Isolated so unit tests can monkeypatch it without installing torch.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise EmbeddingExtraMissing(
            "sentence-transformers is not installed. Install the optional "
            "extra: pip install 'livegraph[semantic]'"
        ) from exc
    return SentenceTransformer


class LocalSTProvider:
    """``EmbeddingProvider`` backed by a locally-loaded sentence-transformer.

    The model is loaded eagerly on construction; the surrounding code is
    responsible for deferring construction until embeddings are actually
    needed (the [semantic] extra may be absent).
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2",
                 batch_size: int = 32) -> None:
        try:
            SentenceTransformerCls = _import_sentence_transformers()
        except ImportError as exc:
            raise EmbeddingExtraMissing(
                "sentence-transformers is not installed. Install the optional "
                "extra: pip install 'livegraph[semantic]'"
            ) from exc
        self._model = SentenceTransformerCls(model_name)
        self.name = model_name
        self.batch_size = batch_size
        self.dimensions = int(
            self._get_dimensions(self._model)
        )

    @staticmethod
    def _get_dimensions(model: Any) -> int:
        """Use the non-deprecated dimension API when available."""
        for attr in ("get_embedding_dimension",
                     "get_sentence_embedding_dimension"):
            getter = getattr(model, attr, None)
            if getter is not None:
                return int(getter())
        raise RuntimeError(
            "Model does not expose a get_embedding_dimension() or "
            "get_sentence_embedding_dimension() method"
        )

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(texts, batch_size=self.batch_size)
        return vectors.tolist()
