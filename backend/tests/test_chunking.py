import pytest

from app.document_processing.chunking import ChunkDraft, split_text


@pytest.mark.parametrize("text", ["", "   ", "\n\t\n"])
def test_empty_or_whitespace_only_text_returns_no_chunks(text: str) -> None:
    assert split_text(text) == []


def test_short_text_returns_one_exact_chunk() -> None:
    assert split_text("人工智能课程") == [
        ChunkDraft(
            text="人工智能课程",
            sequence=0,
            start_offset=0,
            end_offset=6,
        )
    ]


def test_text_equal_to_chunk_size_returns_one_chunk() -> None:
    assert split_text("abcdefgh", chunk_size=8, overlap=2) == [
        ChunkDraft(
            text="abcdefgh",
            sequence=0,
            start_offset=0,
            end_offset=8,
        )
    ]


def test_long_text_uses_hard_boundaries_and_overlap() -> None:
    drafts = split_text("abcdefghij", chunk_size=6, overlap=2)

    assert drafts == [
        ChunkDraft("abcdef", 0, 0, 6),
        ChunkDraft("efghij", 1, 4, 10),
    ]


def test_chinese_sentence_boundary_is_preferred() -> None:
    text = "甲乙丙丁戊。己庚辛壬癸子"

    drafts = split_text(text, chunk_size=8, overlap=2)

    assert drafts[0] == ChunkDraft("甲乙丙丁戊。", 0, 0, 6)
    assert drafts[1] == ChunkDraft(text[4:12], 1, 4, 12)


@pytest.mark.parametrize(
    ("text", "expected_first_chunk"),
    [
        ("first line\nsecond line", "first line\n"),
        ("First sentence. Next sentence", "First sentence."),
    ],
)
def test_newline_and_english_sentence_boundaries_are_preferred(
    text: str,
    expected_first_chunk: str,
) -> None:
    drafts = split_text(text, chunk_size=16, overlap=2)

    assert drafts[0].text == expected_first_chunk


def test_every_chunk_matches_its_original_text_slice() -> None:
    text = "第一句。第二句比较长，需要继续分块。Third sentence without spaces.结束。"

    drafts = split_text(text, chunk_size=14, overlap=3)

    assert [draft.sequence for draft in drafts] == list(range(len(drafts)))
    for draft in drafts:
        assert draft.text == text[draft.start_offset : draft.end_offset]
        assert 0 < len(draft.text) <= 14


@pytest.mark.parametrize(
    ("chunk_size", "overlap", "message"),
    [
        (0, 0, "chunk_size must be greater than zero"),
        (-1, 0, "chunk_size must be greater than zero"),
        (8, -1, "overlap must be at least zero and smaller than chunk_size"),
        (8, 8, "overlap must be at least zero and smaller than chunk_size"),
        (8, 9, "overlap must be at least zero and smaller than chunk_size"),
    ],
)
def test_invalid_chunk_parameters_are_rejected(
    chunk_size: int,
    overlap: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        split_text("text", chunk_size=chunk_size, overlap=overlap)
