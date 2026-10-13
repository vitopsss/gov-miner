from __future__ import annotations

import re
from dataclasses import dataclass


ACORDAO_RE = re.compile(
    r"(?im)(?:^|\n)\s*(AC[ÓO]RD[ÃA]O\s*(?:N[º°.]?|NO\.?)\s*"
    r"[0-9A-Z./-]+(?:\s*[-–]\s*TCE[^\n]*)?)"
)


@dataclass
class TextChunk:
    text: str
    index: int
    document_hint: str | None = None


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_tce_documents(text: str) -> list[TextChunk]:
    text = normalize_text(text)
    matches = list(ACORDAO_RE.finditer(text))
    if not matches:
        return [TextChunk(text=text, index=1, document_hint=None)] if text else []

    chunks: list[TextChunk] = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        chunk_text = text[start:end].strip()
        chunks.append(
            TextChunk(
                text=chunk_text,
                index=idx + 1,
                document_hint=match.group(1).strip(),
            )
        )
    return chunks

