"""Split Markdown documents into one chunk per `##` section."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Chunk:
    chunk_id: str  # "<doc_id>#<section>"
    doc_id: str
    section: str
    text: str  # "# <doc title>\n## <section>\n<body>" — embedded and shown to the LLM


def _doc_title(lines: list[str], fallback: str) -> str:
    for line in lines:
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _split_sections(lines: list[str], title: str) -> list[tuple[str, list[str]]]:
    """Group lines under their `##` heading. Text before the first `##` goes under the doc title."""
    sections: list[tuple[str, list[str]]] = [(title, [])]
    for line in lines:
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif line.startswith("# "):
            continue  # the H1 title itself is not body text
        else:
            sections[-1][1].append(line)
    return sections


def _unique(name: str, seen: dict[str, int]) -> str:
    seen[name] = seen.get(name, 0) + 1
    return name if seen[name] == 1 else f"{name}-{seen[name]}"


def split_markdown(doc_id: str, markdown: str) -> list[Chunk]:
    lines = markdown.splitlines()
    title = _doc_title(lines, fallback=doc_id)
    chunks: list[Chunk] = []
    seen: dict[str, int] = {}
    for section, body_lines in _split_sections(lines, title):
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        section = _unique(section, seen)
        chunks.append(
            Chunk(
                chunk_id=f"{doc_id}#{section}",
                doc_id=doc_id,
                section=section,
                text=f"# {title}\n## {section}\n{body}",
            )
        )
    return chunks


def load_tenant_chunks(docs_dir: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(docs_dir.glob("*.md")):
        chunks.extend(split_markdown(path.stem, path.read_text(encoding="utf-8")))
    return chunks
