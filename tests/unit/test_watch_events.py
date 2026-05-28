from pathlib import Path

from livegraph.watch.events import ChangeBatch, ChangeEvent


def E(kind, p):
    return ChangeEvent(kind=kind, path=Path(p))


def test_empty_batch_is_empty():
    b = ChangeBatch.empty()
    assert b.modified == set()
    assert b.deleted == set()
    assert b.is_empty()


def test_modified_plus_modified_collapses():
    b = ChangeBatch.empty().merge(E("modified", "a.py")).merge(E("modified", "a.py"))
    assert b.modified == {Path("a.py")}
    assert b.deleted == set()


def test_created_treated_as_modified():
    b = ChangeBatch.empty().merge(E("created", "a.py"))
    assert b.modified == {Path("a.py")}


def test_created_then_deleted_cancels():
    b = (
        ChangeBatch.empty()
        .merge(E("created", "a.py"))
        .merge(E("deleted", "a.py"))
    )
    assert b.is_empty()


def test_modified_then_deleted_becomes_deleted():
    b = (
        ChangeBatch.empty()
        .merge(E("modified", "a.py"))
        .merge(E("deleted", "a.py"))
    )
    assert b.modified == set()
    assert b.deleted == {Path("a.py")}


def test_deleted_then_created_becomes_modified():
    b = (
        ChangeBatch.empty()
        .merge(E("deleted", "a.py"))
        .merge(E("created", "a.py"))
    )
    assert b.modified == {Path("a.py")}
    assert b.deleted == set()
