import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from pydantic import Field, ValidationError

from deal_intel.contracts.access import AccessLevel
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.evidence import ChunkKind, EvidenceChunk
from deal_intel.contracts.reference import (
    ACCOUNT_ID_PATTERN,
    CALL_ID_PATTERN,
    OPPORTUNITY_ID_PATTERN,
)
from deal_intel.retrieval.chunkers.base import (
    ChunkDraft,
    IngestContext,
    SourceFormatError,
    make_chunk,
)
from deal_intel.retrieval.dataset import relative_to_root, transcript_paths

TITLE_LINE = re.compile(r"^# Transcript: (?P<call_id>\S+) - (?P<title>.+)$")
HEADER_LINE = re.compile(r"^\*\*(?P<label>[^*]+):\*\*\s*(?P<value>.*?)\s*$")
BLANK_LINE = re.compile(r"\n\s*\n")
HEADER_FIELDS: dict[str, str] = {
    "Opportunity": "opportunity_id",
    "Account": "account_id",
    "Date": "call_date",
    "Source access level": "access_level",
}
SPEAKER_SEPARATOR = ": "
SPEAKER_TITLE_SEPARATOR = ", "
SPEAKER_LIST_SEPARATOR = ", "

# Six turns stepping by five: the last turn of one window opens the next, which keeps a
# question and its answer together across a boundary.
WINDOW_TURNS = 6
WINDOW_OVERLAP = 1
# A tail window adding fewer new turns than this merges into the previous one, so no window
# is an orphaned line and none exceeds WINDOW_TURNS + MIN_TAIL_NEW_TURNS - 1 turns.
MIN_TAIL_NEW_TURNS = 3


class TranscriptTurn(StrictModel):
    speaker_label: str = Field(min_length=1)
    text: str = Field(min_length=1)

    @property
    def speaker(self) -> str:
        """The name without the title that some turns add, e.g. "Priya Sato"."""
        return self.speaker_label.split(SPEAKER_TITLE_SEPARATOR)[0]

    def render(self) -> str:
        return f"{self.speaker_label}{SPEAKER_SEPARATOR}{self.text}"


class Transcript(StrictModel):
    call_id: str = Field(pattern=CALL_ID_PATTERN)
    title: str
    opportunity_id: str = Field(pattern=OPPORTUNITY_ID_PATTERN)
    account_id: str = Field(pattern=ACCOUNT_ID_PATTERN)
    call_date: date
    access_level: AccessLevel
    turns: list[TranscriptTurn] = Field(min_length=1)


def chunk_transcripts(context: IngestContext) -> list[EvidenceChunk]:
    return [
        make_chunk(context, draft)
        for path in transcript_paths(context.data_root)
        for draft in transcript_drafts(context.data_root, path)
    ]


def transcript_drafts(data_root: Path, path: Path) -> list[ChunkDraft]:
    transcript = parse_transcript(path)
    relative_path = relative_to_root(data_root, path)
    return [
        window_draft(transcript, relative_path, segment, turns)
        for segment, turns in enumerate(window_turns(transcript.turns), start=1)
    ]


def parse_transcript(path: Path) -> Transcript:
    lines = path.read_text(encoding="utf-8").splitlines()
    title = TITLE_LINE.match(lines[0].strip()) if lines else None
    if title is None:
        raise SourceFormatError(path, "first line must be '# Transcript: <call id> - <title>'")
    header, body = split_header(lines[1:])
    try:
        return Transcript(
            call_id=title["call_id"],
            title=title["title"],
            **header_values(path, header),
            turns=parse_turns(path, body),
        )
    except ValidationError as error:
        raise SourceFormatError(path, str(error)) from error


def split_header(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    header: dict[str, str] = {}
    for index, line in enumerate(lines):
        match = HEADER_LINE.match(line)
        if match:
            header[match["label"]] = match["value"]
        elif line.strip():
            return header, lines[index:]
    return header, []


def header_values(path: Path, header: dict[str, str]) -> dict[str, str]:
    missing = [label for label in HEADER_FIELDS if label not in header]
    if missing:
        raise SourceFormatError(path, f"missing header fields {missing}")
    return {field: header[label] for label, field in HEADER_FIELDS.items()}


def parse_turns(path: Path, body: list[str]) -> list[TranscriptTurn]:
    paragraphs = [block for block in BLANK_LINE.split("\n".join(body)) if block.strip()]
    return [parse_turn(path, number, block) for number, block in enumerate(paragraphs, start=1)]


def parse_turn(path: Path, number: int, block: str) -> TranscriptTurn:
    # partition, not split: a later ": " belongs to the sentence.
    speaker_label, separator, text = " ".join(block.split()).partition(SPEAKER_SEPARATOR)
    if not separator:
        # The turn text is evidence and may be restricted, so the error names only its position.
        raise SourceFormatError(path, f"turn {number} is not in 'Speaker: text' form")
    return TranscriptTurn(speaker_label=speaker_label, text=text)


def window_bounds(turn_count: int) -> list[tuple[int, int]]:
    step = WINDOW_TURNS - WINDOW_OVERLAP
    bounds: list[tuple[int, int]] = []
    start = 0
    while True:
        end = min(start + WINDOW_TURNS, turn_count)
        bounds.append((start, end))
        if end >= turn_count:
            break
        start += step
    if len(bounds) > 1 and bounds[-1][1] - bounds[-2][1] < MIN_TAIL_NEW_TURNS:
        bounds[-2:] = [(bounds[-2][0], turn_count)]
    return bounds


def window_turns[TurnT](turns: Sequence[TurnT]) -> list[list[TurnT]]:
    return [list(turns[start:end]) for start, end in window_bounds(len(turns))]


def window_draft(
    transcript: Transcript, relative_path: str, segment: int, turns: list[TranscriptTurn]
) -> ChunkDraft:
    # The header makes every window self-describing when it is read on its own.
    header = (
        f"Call {transcript.call_id} | Opportunity {transcript.opportunity_id} | "
        f"Date {transcript.call_date.isoformat()} | Access {transcript.access_level} | "
        f"Segment {segment}"
    )
    lines = [header, f"Title: {transcript.title}", *(turn.render() for turn in turns)]
    speakers = list(dict.fromkeys(turn.speaker for turn in turns))
    return ChunkDraft(
        kind=ChunkKind.TRANSCRIPT,
        source_key=transcript.call_id,
        segment=segment,
        relative_path=relative_path,
        source_id=transcript.call_id,
        opportunity_id=transcript.opportunity_id,
        account_id=transcript.account_id,
        access_level=transcript.access_level,
        text="\n".join(lines),
        metadata={"title": transcript.title, "segment": segment, "turn_count": len(turns)},
        event_date=transcript.call_date,
        author_or_speakers=SPEAKER_LIST_SEPARATOR.join(speakers),
    )
