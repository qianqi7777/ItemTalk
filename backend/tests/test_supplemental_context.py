"""Legacy immutable chunks must not turn a split table cell into an instruction."""

from uuid import uuid4

from app.services import supplemental_index
from app.services.knowledge import document_chunks


def chunk(text, number=1, **overrides):
    result = dict(
        id=uuid4(),
        supplemental_document_id="manual-1",
        document_version_id="version-1",
        chunk_no=number,
        page_start=1,
        page_end=1,
        content=text,
        embedding_model="embed",
        embedding_revision="v1",
    )
    return {**result, **overrides}


def window(row, before=None, after=None):
    page = "".join(r["content"] for r in (before, row, after) if r)
    return supplemental_index.context_window(row, before, after, page_text=page)


def test_reconstructs_complete_table_row_at_legacy_boundary():
    first = chunk("Notes\n" * 325 + "Function | Left (L) | Right (R)\nPlay/Pause | ●")
    second = chunk("● | ●●\nAnswer Call | ● | ●\n", 2)
    excerpt, used = window(first, None, second)
    assert "Play/Pause | ●● | ●●" in excerpt
    assert not excerpt.endswith("Play/Pause | ●")
    assert len(excerpt) <= 2000
    assert {r["id"] for r in used} == {first["id"], second["id"]}


def test_boundary_can_start_in_previous_chunk():
    previous = chunk("Function | Left | Right\nPlay/Pause | ●")
    current = chunk("● | ●●\nAnswer Call | ● | ●\n", 2)
    excerpt, used = window(current, previous, None)
    assert excerpt.startswith("Play/Pause | ●● | ●●\n")
    assert len(used) == 2


def test_does_not_join_other_page_version_or_nonadjacent_chunk():
    first = chunk("Play/Pause | ●")
    for changes in (
        {"page_start": 2, "page_end": 2},
        {"supplemental_document_id": "other"},
        {"document_version_id": "other"},
        {"embedding_revision": "other"},
        {"chunk_no": 3},
    ):
        values = {"chunk_no": 2, **changes}
        other = chunk("● | ●●\n", **values)
        excerpt, used = window(first, None, other)
        assert "Play/Pause | ●●" not in excerpt
        assert len(used) == 1


def test_complete_newline_does_not_merge_two_distinct_table_rows():
    first = chunk("Play/Pause | ●● | ●●\n")
    second = chunk("Answer Call | ● | ●\n", 2)
    excerpt, used = window(first, None, second)
    assert excerpt == first["content"]
    assert used == [first]


def test_oversized_single_row_is_not_cut_into_a_false_complete_row():
    first = chunk("Heading\n" + "A" * 1990)
    second = chunk("B" * 1990 + "\nNext complete row\n", 2)
    excerpt, _ = window(first, None, second)
    assert len(excerpt) <= 2000
    assert "A" not in excerpt and "B" not in excerpt


def test_unrecoverable_row_spanning_three_chunks_does_not_expose_its_tail():
    before = chunk("A" * 2000)
    current = chunk("B" * 2000, 2)
    after = chunk("C" * 100 + "\nNext complete row\n", 3)
    excerpt, _ = window(current, before, after)
    assert not excerpt


def test_skipped_whitespace_slice_is_not_a_contiguous_table_cell():
    prefix = "Heading\n" + "a\n" * 989 + "Play/Pause | ●"
    prefix = prefix.rjust(2000)
    raw = prefix + "\n " * 1000 + "● | ●●\n"
    pieces = document_chunks([(1, raw)])
    assert len(pieces) == 2
    first, second = [chunk(t, n) for n, _, t in pieces]
    excerpt, _ = supplemental_index.context_window(first, None, second, page_text=raw)
    assert "Play/Pause | ●● | ●●" not in excerpt
