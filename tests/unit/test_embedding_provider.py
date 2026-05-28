import pytest

from livegraph.semantic.provider import (
    EmbeddingExtraMissing, EmbeddingDimensionMismatch,
    LocalSTProvider,
)


def test_extra_missing_exception_carries_install_hint():
    err = EmbeddingExtraMissing("install hint")
    assert "install" in str(err).lower()


def test_dimension_mismatch_exception_carries_dimensions():
    err = EmbeddingDimensionMismatch(384, 768)
    assert err.existing == 384
    assert err.new == 768
    assert "384" in str(err) and "768" in str(err)
    assert "--rebuild" in str(err)


def test_local_st_provider_raises_when_extra_missing(monkeypatch):
    def _raise_import_error():
        raise ImportError("No module named 'sentence_transformers'")

    monkeypatch.setattr(
        "livegraph.semantic.provider._import_sentence_transformers",
        _raise_import_error,
    )
    with pytest.raises(EmbeddingExtraMissing) as exc:
        LocalSTProvider(model_name="all-MiniLM-L6-v2")
    assert "semantic" in str(exc.value).lower()


def test_local_st_provider_constructs_with_mocked_st(monkeypatch):
    encoded_calls: list[list[str]] = []

    class _FakeModel:
        def get_sentence_embedding_dimension(self) -> int:
            return 384

        def encode(self, texts, batch_size=32):
            encoded_calls.append(list(texts))
            class _FakeArr:
                def __init__(self, data):
                    self._data = data
                def tolist(self):
                    return self._data
            return _FakeArr([[0.1] * 384 for _ in texts])

    def _fake_import():
        def _fake_factory(model_name):
            assert model_name == "all-MiniLM-L6-v2"
            return _FakeModel()
        return _fake_factory

    monkeypatch.setattr(
        "livegraph.semantic.provider._import_sentence_transformers",
        _fake_import,
    )

    provider = LocalSTProvider(model_name="all-MiniLM-L6-v2",
                               batch_size=16)
    assert provider.name == "all-MiniLM-L6-v2"
    assert provider.dimensions == 384
    assert provider.batch_size == 16

    vectors = provider.encode(["hello world", "foo bar"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 384
    assert encoded_calls == [["hello world", "foo bar"]]


def test_local_st_provider_encode_empty_returns_empty(monkeypatch):
    encoded_calls: list[list[str]] = []

    class _FakeModel:
        def get_sentence_embedding_dimension(self) -> int:
            return 384
        def encode(self, texts, batch_size=32):
            encoded_calls.append(list(texts))
            return []

    monkeypatch.setattr(
        "livegraph.semantic.provider._import_sentence_transformers",
        lambda: (lambda model_name: _FakeModel()),
    )
    provider = LocalSTProvider(model_name="all-MiniLM-L6-v2")
    assert provider.encode([]) == []
    assert encoded_calls == []
