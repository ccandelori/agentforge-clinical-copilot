"""Standalone vision-extraction demo (W2 Task 11).

Reads a lab-PDF off disk, runs it through the ``attach_and_extract``
pipeline, and prints the validated ``LabPdfExtraction`` to stdout.
The script intentionally short-circuits the JWT-validated
``get_document_bytes`` HTTP fetch — for the demo we read bytes
directly from a file path so you can point it at any sample PDF.
The full HTTP pipeline (fetch → render → extract → persist) plugs
into the orchestrator separately.

Run with:

    export ANTHROPIC_API_KEY=...
    cd sidecar

    # Default: extracts the bundled mock lab PDF
    uv run python scripts/extraction_demo.py

    # Or point at any PDF on disk
    uv run python scripts/extraction_demo.py /path/to/your/lab.pdf

The default PDF lives at ``data/samples/sample-lab.pdf``, generated
by ``scripts/generate_mock_lab.py``. Re-run that script to see how
the synthetic-but-realistic source data is composed.

Optional flags:

    --document-id N   (default 1) — passed through to the extraction
    --patient-id N    (default 1) — passed through to the extraction
    --json            print the full LabPdfExtraction as JSON
    --dpi N           render DPI (default 150)

Costs: one Anthropic vision call per invocation. Keep the PDF small;
this is a demo, not a benchmark.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from agentforge.tools.attach_and_extract import (
    DEFAULT_DPI,
    PdfRenderer,
    VisionExtractor,
)


async def run(args: argparse.Namespace) -> int:
    pdf_path: Path = args.pdf.resolve()
    if not pdf_path.is_file():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        return 2

    pdf_bytes = pdf_path.read_bytes()
    print(f"Loaded {len(pdf_bytes):,} bytes from {pdf_path.name}")

    renderer = PdfRenderer(dpi=args.dpi)
    pages = renderer.render_pages(pdf_bytes)
    page_summaries = ", ".join(
        f"p{p.page_number}={p.pixel_width}x{p.pixel_height}" for p in pages
    )
    print(f"Rendered {len(pages)} page(s): {page_summaries}")

    extractor = VisionExtractor()
    print("Calling Claude vision...")
    result = await extractor.extract(
        pages=pages,
        document_id=args.document_id,
        patient_id=args.patient_id,
    )

    extraction = result.extraction
    print()
    print("=" * 60)
    print("Vision extraction complete")
    print("=" * 60)
    print(f"  model:                {result.model}")
    print(f"  input_tokens:         {result.input_tokens:,}")
    print(f"  output_tokens:        {result.output_tokens:,}")
    print(f"  document_id:          {extraction.document_id}")
    print(f"  patient_id:           {extraction.patient_id}")
    print(f"  ordering_provider:    {extraction.ordering_provider}")
    print(f"  accession_number:     {extraction.accession_number}")
    print(f"  extraction_confidence:{extraction.extraction_confidence}")
    print(f"  values: {len(extraction.values)} extracted")
    for i, value in enumerate(extraction.values, start=1):
        bbox = value.citation.page_bbox
        bbox_str = (
            f"page={bbox.page} "
            f"({bbox.x0:.2f},{bbox.y0:.2f})-({bbox.x1:.2f},{bbox.y1:.2f}) "
            f"conf={bbox.bbox_confidence:.2f}"
            if bbox is not None
            else "(no bbox)"
        )
        unit = f" {value.unit}" if value.unit else ""
        flag = (
            f" [{value.abnormal_flag.value}]"
            if value.abnormal_flag.value != "unknown"
            else ""
        )
        print(f"  [{i}] {value.test_name}: {value.value}{unit}{flag}")
        print(f"      citation: {bbox_str}")
        print(f"      quote:    \"{value.citation.quote_or_value}\"")

    if extraction.unsupported_fields:
        print(f"  unsupported_fields ({len(extraction.unsupported_fields)}):")
        for f in extraction.unsupported_fields:
            print(f"    - {f}")

    if args.json:
        print()
        print("=" * 60)
        print("Full extraction JSON")
        print("=" * 60)
        print(json.dumps(extraction.model_dump(mode="json"), indent=2, default=str))

    return 0


_DEFAULT_SAMPLE_PDF = (
    Path(__file__).resolve().parents[1] / "data" / "samples" / "sample-lab.pdf"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "pdf",
        type=Path,
        nargs="?",
        default=_DEFAULT_SAMPLE_PDF,
        help=(
            f"Path to a lab-result PDF on disk. "
            f"Defaults to the bundled mock lab at {_DEFAULT_SAMPLE_PDF.name}."
        ),
    )
    parser.add_argument("--document-id", type=int, default=1)
    parser.add_argument("--patient-id", type=int, default=1)
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_DPI,
        help=f"Render DPI (default {DEFAULT_DPI}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full LabPdfExtraction as JSON after the summary.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
