#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pymysql>=1.1"]
# ///
"""Load Synthea-generated FHIR DocumentReferences into OpenEMR's pnotes table.

This script bridges the gap left by OpenEMR's CCDA importer, which does not
populate pnotes / form_clinical_notes (CCDA is a structured document format
without free-text clinical notes — see docs/test-data.md). Synthea's FHIR
output, however, includes one DocumentReference per encounter with a
markdown-formatted note body. We extract those and insert them as pnotes.

Usage:
    uv run scripts/seed/load_synthea_notes.py \\
        --fhir-dir ~/Desktop/Gauntlet/synthea/output_20patients/fhir \\
        --db-host 127.0.0.1 --db-port 8320

The script is idempotent on a fingerprint of (pid, date, title): re-running
it will not duplicate existing notes.
"""
from __future__ import annotations

import argparse
import base64
import datetime
import glob
import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pymysql


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fhir-dir",
        required=True,
        help="Directory containing Synthea FHIR JSON bundles",
    )
    parser.add_argument("--db-host", default="127.0.0.1")
    parser.add_argument("--db-port", type=int, default=8320)
    parser.add_argument("--db-user", default="openemr")
    parser.add_argument("--db-pass", default="openemr")
    parser.add_argument("--db-name", default="openemr")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report counts but do not INSERT",
    )
    return parser.parse_args()


def iter_patient_bundles(fhir_dir: Path) -> Iterator[Path]:
    """Yield FHIR bundle files that contain a Patient resource."""
    for path in sorted(fhir_dir.glob("*.json")):
        # Skip metadata files (hospitalInformation*.json, practitionerInformation*.json)
        name = path.name
        if name.startswith("hospitalInformation") or name.startswith(
            "practitionerInformation"
        ):
            continue
        yield path


def extract_patient(bundle: dict) -> dict | None:
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        if resource.get("resourceType") == "Patient":
            return resource
    return None


def extract_document_references(bundle: dict) -> list[dict]:
    return [
        entry["resource"]
        for entry in bundle.get("entry", [])
        if entry.get("resource", {}).get("resourceType") == "DocumentReference"
    ]


def patient_name_dob(patient: dict) -> list[tuple[str, str, str]]:
    """Return candidate (fname, lname, DOB) tuples to try for a DB match.

    Synthea Patient resources have all given names in `name[0].given`
    (e.g. ["Kandi717", "Maryellen651"]). OpenEMR's CCDA importer stores
    them as `fname` differently per patient: sometimes just given[0],
    sometimes the full space-joined string. Return both candidates so
    the lookup falls back gracefully.
    """
    names = patient.get("name", [])
    if not names:
        return []
    name = names[0]
    given = name.get("given", [])
    family = name.get("family")
    dob = patient.get("birthDate")
    if not given or not family or not dob:
        return []
    candidates = [(given[0], family, dob)]
    if len(given) > 1:
        candidates.append((" ".join(given), family, dob))
    return candidates


def doc_note_body(doc: dict) -> str | None:
    contents = doc.get("content", [])
    if not contents:
        return None
    attachment = contents[0].get("attachment", {})
    data_b64 = attachment.get("data")
    if not data_b64:
        return None
    try:
        return base64.b64decode(data_b64).decode("utf-8", errors="replace")
    except Exception:
        return None


def doc_title(doc: dict) -> str:
    type_ = doc.get("type", {})
    for coding in type_.get("coding", []):
        if "display" in coding:
            return str(coding["display"])
    return "Clinical note"


def doc_date(doc: dict) -> str | None:
    """Return YYYY-MM-DD HH:MM:SS string or None."""
    raw = doc.get("date")
    if not raw:
        return None
    # FHIR dates are ISO8601 with Z or timezone — strip to MariaDB DATETIME format
    try:
        dt = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def lookup_pid(
    cursor: pymysql.cursors.Cursor,
    fname: str,
    lname: str,
    dob: str,
) -> int | None:
    cursor.execute(
        "SELECT pid FROM patient_data WHERE fname = %s AND lname = %s AND DOB = %s",
        (fname, lname, dob),
    )
    row = cursor.fetchone()
    return int(row[0]) if row else None


def already_loaded(
    cursor: pymysql.cursors.Cursor,
    pid: int,
    note_date: str,
    title: str,
) -> bool:
    cursor.execute(
        "SELECT 1 FROM pnotes WHERE pid = %s AND date = %s AND title = %s LIMIT 1",
        (pid, note_date, title),
    )
    return cursor.fetchone() is not None


def insert_note(
    cursor: pymysql.cursors.Cursor,
    pid: int,
    note_date: str,
    title: str,
    body: str,
) -> None:
    cursor.execute(
        """
        INSERT INTO pnotes (
            date, body, pid, user, groupname, activity, authorized,
            title, message_status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (note_date, body, pid, "synthea-loader", "Default", 1, 1, title, "New"),
    )


def main() -> int:
    args = parse_args()
    fhir_dir = Path(args.fhir_dir).expanduser().resolve()
    if not fhir_dir.is_dir():
        print(f"FHIR dir not found: {fhir_dir}", file=sys.stderr)
        return 1

    bundle_files = list(iter_patient_bundles(fhir_dir))
    if not bundle_files:
        print(f"No patient bundles in {fhir_dir}", file=sys.stderr)
        return 1

    print(f"Found {len(bundle_files)} patient bundle(s) in {fhir_dir}")

    cnx = pymysql.connect(
        host=args.db_host,
        port=args.db_port,
        user=args.db_user,
        password=args.db_pass,
        database=args.db_name,
        autocommit=False,
    )
    try:
        with cnx.cursor() as cursor:
            stats = {"matched": 0, "missing_pid": 0, "inserted": 0, "skipped": 0}
            for bundle_path in bundle_files:
                with bundle_path.open() as f:
                    bundle = json.load(f)

                patient = extract_patient(bundle)
                if not patient:
                    continue
                candidates = patient_name_dob(patient)
                if not candidates:
                    continue

                pid = None
                for fname, lname, dob in candidates:
                    pid = lookup_pid(cursor, fname, lname, dob)
                    if pid is not None:
                        break

                if pid is None:
                    stats["missing_pid"] += 1
                    fname, lname, dob = candidates[0]
                    print(
                        f"  no pid for {fname} {lname} ({dob}) — was this patient imported?"
                    )
                    continue
                stats["matched"] += 1

                docs = extract_document_references(bundle)
                for doc in docs:
                    note_date = doc_date(doc)
                    if not note_date:
                        continue
                    title = doc_title(doc)
                    body = doc_note_body(doc)
                    if not body:
                        continue
                    if already_loaded(cursor, pid, note_date, title):
                        stats["skipped"] += 1
                        continue
                    if not args.dry_run:
                        insert_note(cursor, pid, note_date, title, body)
                    stats["inserted"] += 1

            if not args.dry_run:
                cnx.commit()
                print("committed.")
            else:
                print("(dry-run — nothing committed)")

            print(
                f"matched={stats['matched']}  missing_pid={stats['missing_pid']}  "
                f"inserted={stats['inserted']}  skipped_existing={stats['skipped']}"
            )
    finally:
        cnx.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
