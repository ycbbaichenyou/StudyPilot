from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParsedTextUnit:
    """One ordered piece of parsed text and its position in the source file."""

    text: str
    sequence: int
    source_type: str
    source_start: int
    source_end: int
