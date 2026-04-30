"""LangGraph multi-agent orchestration with parallel tool dispatch.

Hosts the Planner, Tool Dispatcher, Synthesizer, and Verifier nodes within
a single process. Up to 3 tools dispatch concurrently within a 4-second
tool-phase budget, bounded by the 7-second total agent deadline.
See ARCHITECTURE.md §3.
"""
