"""Chunk the AgentForge clinical-guideline corpus into a flat
``index.json`` ready for retrieval.

The chunker walks ``sidecar/data/guidelines/{topic}/{document}.md``,
splits each document by Markdown heading, packs section bodies into
chunks of approximately ``--target-tokens`` tokens (default 500),
and writes a single ``index.json`` to the corpus root.

The chunk shape matches what the evidence retriever (Task 15) and the
Citation contract (Task 2, ``SourceType.GUIDELINE``) expect:

- ``doc_id``    — stable handle derived from the file path
- ``section``   — the heading the chunk falls under
- ``version``   — guideline year/version (parsed from the SOURCE
                  block, falls back to the corpus version constant)
- ``chunk_id``  — globally unique handle ``{doc_id}::{section_slug}::{n}``
- ``text``      — the chunk body
- ``token_count``    — measured by tiktoken (cl100k_base) for sizing
- ``source_path``    — relative path to the markdown file (provenance)

Run with: ``uv run python sidecar/scripts/chunk_guidelines.py``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import tiktoken

CORPUS_VERSION = "demo-1.0"
DEFAULT_TARGET_TOKENS = 500
DEFAULT_MAX_TOKENS = 700  # hard cap before forcing a split mid-section


@dataclass(frozen=True)
class Chunk:
    """A single retrieval-ready chunk."""

    doc_id: str
    section: str
    version: str
    chunk_id: str
    text: str
    token_count: int
    source_path: str


def slugify(text: str) -> str:
    """Lowercase / hyphenate a heading into a chunk-id-safe slug."""
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[\s_-]+", "-", text).strip("-")
    return text or "untitled"


def parse_version(markdown: str, fallback: str) -> str:
    """Pull a year/version out of a ``> SOURCE:`` block if present.

    Looks for a 4-digit year following 'SOURCE' or '20xx', falling back
    to ``fallback`` (the corpus version) when nothing matches. Keeps
    the dependency surface tiny — no front-matter parser required.
    """
    source_block = re.search(
        r"^>\s*\*\*SOURCE:\*\*.*?(?=^\s*$)",
        markdown,
        re.MULTILINE | re.DOTALL,
    )
    haystack = source_block.group(0) if source_block else markdown[:500]
    match = re.search(r"\b(20\d{2})\b", haystack)
    return match.group(1) if match else fallback


def split_into_sections(markdown: str) -> list[tuple[str, str]]:
    """Split markdown by H1/H2/H3 headings.

    Returns a list of ``(section_title, body)`` tuples. The body
    excludes the heading line itself but includes everything up to the
    next heading or EOF. The first chunk before any heading is
    returned with section title 'preamble' so the SOURCE/NOTICE block
    is preserved in the corpus.
    """
    lines = markdown.splitlines()
    sections: list[tuple[str, str]] = []
    current_title = "preamble"
    current_body: list[str] = []

    heading_re = re.compile(r"^(#{1,3})\s+(.+)$")

    for line in lines:
        m = heading_re.match(line)
        if m:
            if current_body or current_title != "preamble":
                sections.append((current_title, "\n".join(current_body).strip()))
            current_title = m.group(2).strip()
            current_body = []
        else:
            current_body.append(line)

    if current_body or current_title != "preamble":
        sections.append((current_title, "\n".join(current_body).strip()))

    return [(title, body) for title, body in sections if body]


def count_tokens(encoder: tiktoken.Encoding, text: str) -> int:
    return len(encoder.encode(text))


def pack_section(
    *,
    encoder: tiktoken.Encoding,
    title: str,
    body: str,
    target_tokens: int,
    max_tokens: int,
) -> list[str]:
    """Greedy-pack section body into 1+ sub-chunks.

    A short section returns ``[body]``; a longer one is split on
    paragraph boundaries (blank lines) to keep semantic units intact.
    Each sub-chunk includes the ``title`` as a leading line so the
    chunk is self-explanatory when retrieved out of context.
    """
    paragraphs = re.split(r"\n\s*\n", body)
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    title_prefix = f"## {title}\n\n"
    title_tokens = count_tokens(encoder, title_prefix)

    for para in paragraphs:
        if not para.strip():
            continue
        para_tokens = count_tokens(encoder, para)
        prospective = current_tokens + para_tokens
        # Pack until we exceed target; if a single paragraph blows past
        # max_tokens on its own, emit it solo (better than hard-cutting
        # mid-paragraph for the demo corpus).
        if current and (prospective > target_tokens):
            chunks.append(title_prefix + "\n\n".join(current))
            current = [para]
            current_tokens = title_tokens + para_tokens
        else:
            current.append(para)
            current_tokens = title_tokens + prospective

    if current:
        chunks.append(title_prefix + "\n\n".join(current))

    return chunks


def chunk_document(
    *,
    path: Path,
    corpus_root: Path,
    encoder: tiktoken.Encoding,
    target_tokens: int,
    max_tokens: int,
) -> list[Chunk]:
    markdown = path.read_text(encoding="utf-8")
    if not markdown.strip():
        return []

    rel_path = path.relative_to(corpus_root)
    doc_id = slugify(rel_path.with_suffix("").as_posix().replace("/", "_"))
    version = parse_version(markdown, CORPUS_VERSION)

    sections = split_into_sections(markdown)
    chunks: list[Chunk] = []
    for section_title, body in sections:
        section_slug = slugify(section_title)
        sub_texts = pack_section(
            encoder=encoder,
            title=section_title,
            body=body,
            target_tokens=target_tokens,
            max_tokens=max_tokens,
        )
        for i, text in enumerate(sub_texts):
            chunk_id = f"{doc_id}::{section_slug}::{i}"
            chunks.append(
                Chunk(
                    doc_id=doc_id,
                    section=section_title,
                    version=version,
                    chunk_id=chunk_id,
                    text=text,
                    token_count=count_tokens(encoder, text),
                    source_path=rel_path.as_posix(),
                )
            )

    return chunks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "guidelines",
        help="Root directory holding {topic}/{document}.md files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output index.json path (defaults to <corpus_root>/index.json).",
    )
    parser.add_argument(
        "--target-tokens",
        type=int,
        default=DEFAULT_TARGET_TOKENS,
        help="Soft target chunk size in tokens.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        help="Hard ceiling — currently informational; pack_section's logic targets soft.",
    )
    args = parser.parse_args(argv)

    corpus_root: Path = args.corpus_root.resolve()
    if not corpus_root.is_dir():
        print(f"corpus root not found: {corpus_root}", file=sys.stderr)
        return 2

    output_path: Path = (args.output or (corpus_root / "index.json")).resolve()
    encoder = tiktoken.get_encoding("cl100k_base")

    md_files = sorted(p for p in corpus_root.rglob("*.md") if p.name != "NOTICE.md")
    all_chunks: list[Chunk] = []
    for md in md_files:
        all_chunks.extend(
            chunk_document(
                path=md,
                corpus_root=corpus_root,
                encoder=encoder,
                target_tokens=args.target_tokens,
                max_tokens=args.max_tokens,
            )
        )

    index = {
        "version": CORPUS_VERSION,
        "target_tokens": args.target_tokens,
        "chunk_count": len(all_chunks),
        "doc_count": len(md_files),
        "chunks": [asdict(c) for c in all_chunks],
    }
    output_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")

    avg_tokens = (
        sum(c.token_count for c in all_chunks) / len(all_chunks) if all_chunks else 0
    )
    display_path = (
        output_path.relative_to(Path.cwd())
        if output_path.is_relative_to(Path.cwd())
        else output_path
    )
    print(
        f"Wrote {display_path} "
        f"({len(md_files)} docs, {len(all_chunks)} chunks, "
        f"avg {avg_tokens:.0f} tokens/chunk)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
