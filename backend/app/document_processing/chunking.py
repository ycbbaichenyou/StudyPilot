from dataclasses import dataclass


DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100
NATURAL_BOUNDARIES = ("\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";")


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """One text slice and its exact character offsets in DocumentContent.text."""

    text: str
    sequence: int
    start_offset: int
    end_offset: int


def _find_natural_end(
    text: str,
    *,
    minimum_end: int,
    hard_end: int,
) -> int | None:
    candidates: list[int] = []
    for boundary in NATURAL_BOUNDARIES:
        boundary_start = text.rfind(boundary, minimum_end, hard_end)
        if boundary_start != -1:
            candidates.append(boundary_start + len(boundary))
    return max(candidates, default=None)


def split_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[ChunkDraft]:
    """Split one DocumentContent text without crossing its source boundary."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be at least zero and smaller than chunk_size")
    if not text.strip():
        return []

    drafts: list[ChunkDraft] = []
    start_offset = 0

    while start_offset < len(text):
        hard_end = min(start_offset + chunk_size, len(text))
        end_offset = hard_end

        if hard_end < len(text):
            minimum_length = max(chunk_size // 2, overlap + 1)
            natural_end = _find_natural_end(
                text,
                minimum_end=start_offset + minimum_length,
                hard_end=hard_end,
            )
            if natural_end is not None:
                end_offset = natural_end

        chunk_text = text[start_offset:end_offset]
        if chunk_text.strip():
            drafts.append(
                ChunkDraft(
                    text=chunk_text,
                    sequence=len(drafts),
                    start_offset=start_offset,
                    end_offset=end_offset,
                )
            )

        if end_offset == len(text):
            break
        start_offset = end_offset - overlap

    return drafts
