"""Streaming claim-by-claim grounding verifier.

Buffers tokens at sentence boundaries, parses the citation, and validates
the structured assertion against the in-turn tool result cache before
flushing the verified claim to the user. The verifier never retries.
See ARCHITECTURE.md §6.
"""
