"""Wraps evidence as labelled, untrusted data before it reaches a model."""

import re
from collections.abc import Sequence
from html import escape

from deal_intel.contracts.evidence import PackChunk
from deal_intel.contracts.llm import ToolOutput

EVIDENCE_TAG = "evidence"
EMPTY_TOOL_OUTPUT = "No matching evidence."
# Neutralises opening and closing tags alike, so a chunk can neither close its own wrapper nor
# open a fake one that looks like a separate chunk.
EVIDENCE_TAG_PATTERN = re.compile(rf"<(/?\s*){EVIDENCE_TAG}", re.IGNORECASE)


def escape_evidence_tags(text: str) -> str:
    return EVIDENCE_TAG_PATTERN.sub(rf"&lt;\1{EVIDENCE_TAG}", text)


def frame_chunk(chunk: PackChunk) -> str:
    date = chunk.event_date.isoformat() if chunk.event_date else ""
    attributes = (
        f'chunk_id="{escape(chunk.chunk_id)}" citation="{escape(chunk.citation)}" date="{date}"'
    )
    body = escape_evidence_tags(chunk.text)
    return f"<{EVIDENCE_TAG} {attributes}>\n{body}\n</{EVIDENCE_TAG}>"


def frame_chunks(chunks: Sequence[PackChunk]) -> str:
    return "\n".join(frame_chunk(chunk) for chunk in chunks)


def frame_tool_output(output: ToolOutput) -> str:
    parts = [part for part in (output.note, frame_chunks(output.chunks)) if part]
    return "\n".join(parts) if parts else EMPTY_TOOL_OUTPUT
