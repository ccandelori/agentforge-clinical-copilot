"""Langfuse integration with HMAC-keyed pseudonyms for engineering traces.

Logs the shape of an interaction (timing, status, hashes) but never its
substance (no patient_id, no PHI bodies, no prompt content). Distinct from
OpenEMR's legal audit log, which is the medical-record system of record.
See ARCHITECTURE.md §7.
"""
