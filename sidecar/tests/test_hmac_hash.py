"""Tests for the HMAC pseudonymization / payload-hashing helper.

These cover the safety properties the rest of the observability stack
relies on: same input + same key is stable, different keys diverge,
truncation length is fixed, and string/int identifiers normalise to the
same token. See ARCHITECTURE.md S7.2.
"""

from __future__ import annotations

import pytest

from agentforge.observability.hmac_hash import (
    PSEUDONYM_HEX_LENGTH,
    hash_payload,
    pseudonymize,
)

KEY_A = b"key-a-32-bytes-aaaaaaaaaaaaaaaaa"
KEY_B = b"key-b-32-bytes-bbbbbbbbbbbbbbbbb"


# ---------- pseudonymize ----------


def test_pseudonymize_is_deterministic_for_same_input_and_key() -> None:
    assert pseudonymize(123, KEY_A) == pseudonymize(123, KEY_A)
    assert pseudonymize("alice", KEY_A) == pseudonymize("alice", KEY_A)


def test_pseudonymize_different_keys_produce_different_outputs() -> None:
    assert pseudonymize(123, KEY_A) != pseudonymize(123, KEY_B)


def test_pseudonymize_different_inputs_produce_different_outputs() -> None:
    assert pseudonymize(123, KEY_A) != pseudonymize(124, KEY_A)


def test_pseudonymize_truncates_to_fixed_length() -> None:
    token = pseudonymize(123, KEY_A)
    assert len(token) == PSEUDONYM_HEX_LENGTH
    assert all(c in "0123456789abcdef" for c in token)


def test_pseudonymize_normalises_int_and_str_inputs() -> None:
    # The HMAC input is str(raw_id), so 42 and "42" hash identically.
    # Documented: callers wanting the distinction must pre-encode.
    assert pseudonymize(42, KEY_A) == pseudonymize("42", KEY_A)


def test_pseudonymize_rejects_empty_key() -> None:
    with pytest.raises(ValueError):
        pseudonymize(123, b"")


def test_pseudonymize_does_not_leak_raw_id_substring() -> None:
    # Sanity: the hex digest should never contain the obvious decimal
    # spelling of the raw ID. This is a smoke test against a regression
    # to a non-cryptographic hash.
    raw = 1234567890
    token = pseudonymize(raw, KEY_A)
    assert str(raw) not in token


# ---------- hash_payload ----------


def test_hash_payload_is_deterministic_for_str_input() -> None:
    assert hash_payload("hello", KEY_A) == hash_payload("hello", KEY_A)


def test_hash_payload_is_deterministic_for_bytes_input() -> None:
    assert hash_payload(b"hello", KEY_A) == hash_payload(b"hello", KEY_A)


def test_hash_payload_str_and_bytes_match_when_utf8_equivalent() -> None:
    assert hash_payload("hello", KEY_A) == hash_payload(b"hello", KEY_A)


def test_hash_payload_dict_is_order_insensitive() -> None:
    a = hash_payload({"a": 1, "b": 2}, KEY_A)
    b = hash_payload({"b": 2, "a": 1}, KEY_A)
    assert a == b


def test_hash_payload_different_keys_produce_different_outputs() -> None:
    assert hash_payload("hello", KEY_A) != hash_payload("hello", KEY_B)


def test_hash_payload_different_payloads_produce_different_outputs() -> None:
    assert hash_payload("hello", KEY_A) != hash_payload("world", KEY_A)


def test_hash_payload_returns_full_hex_digest() -> None:
    # 64 hex chars == 256 bits == full SHA-256 output.
    assert len(hash_payload("hello", KEY_A)) == 64


def test_hash_payload_rejects_empty_key() -> None:
    with pytest.raises(ValueError):
        hash_payload("hello", b"")
