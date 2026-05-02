"""Versioned prompt loader.

Reads prompt bodies from ``prompts/<version>/<component>.md`` at the
repo root. Which version is active per component is pinned in
``prompts/version.json``. The loader strips the YAML frontmatter block
and any surrounding whitespace, returning a clean prompt body that can
be passed straight to the LLM client as the ``system`` argument.

See ``prompts/README.md`` for the layout and versioning policy.

The loader is cached via :func:`functools.cache`; loading happens
once per process per component. Tests can call
``load_prompt.cache_clear()`` between cases when they need to
exercise the disk path.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Final

_PROMPTS_DIR_NAME: Final[str] = "prompts"
_VERSION_FILE_NAME: Final[str] = "version.json"
_FRONTMATTER_DELIMITER: Final[str] = "---"


class PromptNotFoundError(LookupError):
    """Raised when a requested prompt cannot be resolved.

    Distinct from :class:`FileNotFoundError` so callers (and tests) can
    tell prompt-config problems from generic I/O failures. Three things
    can trip this:

    * the ``prompts/`` directory is missing entirely (deployment bug);
    * the requested ``component`` isn't pinned in ``version.json``;
    * the pinned version directory exists but the component's ``.md``
      file is missing inside it.
    """


def _find_prompts_dir() -> Path:
    """Walk up from this file until we hit a ``prompts/`` sibling.

    The sidecar may be running from any of: the repo checkout, an
    installed wheel inside Docker, or the test runner's working dir.
    Hardcoding an absolute path would break at least one of those, so
    we resolve relative to ``__file__`` and walk parents until the
    repo-root marker (a directory named ``prompts`` containing
    ``version.json``) appears.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / _PROMPTS_DIR_NAME
        if candidate.is_dir() and (candidate / _VERSION_FILE_NAME).is_file():
            return candidate
    raise PromptNotFoundError(
        f"Could not locate '{_PROMPTS_DIR_NAME}/' with '{_VERSION_FILE_NAME}' "
        f"by walking up from {here}. Is the prompts library deployed?"
    )


def _load_version_map(prompts_dir: Path) -> dict[str, str]:
    """Read ``version.json`` and return the component→version mapping.

    Validates shape eagerly so a malformed file fails at startup rather
    than blowing up on the first user turn.
    """
    version_path = prompts_dir / _VERSION_FILE_NAME
    raw = json.loads(version_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise PromptNotFoundError(
            f"{version_path} must contain a JSON object mapping component "
            f"names to version strings; got {type(raw).__name__}."
        )
    mapping: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise PromptNotFoundError(
                f"{version_path} contains a non-string entry: "
                f"{key!r} -> {value!r}."
            )
        mapping[key] = value
    return mapping


def _strip_frontmatter(text: str) -> str:
    """Return ``text`` with a leading YAML frontmatter block removed.

    A frontmatter block is a ``---`` line at the very start of the
    file, a body, then a closing ``---`` line. If the file has no
    frontmatter the input is returned unchanged. Trailing/leading
    whitespace on the resulting body is stripped so prompts round-trip
    exactly to the inline string constants they replaced (the constants
    end on a non-newline character thanks to ``\\`` line continuations
    in the original Python).
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_DELIMITER:
        return text.strip()
    # Find the closing delimiter; bail out if it's missing rather than
    # silently swallowing the entire file as frontmatter.
    for end_idx in range(1, len(lines)):
        if lines[end_idx].strip() == _FRONTMATTER_DELIMITER:
            body_lines = lines[end_idx + 1 :]
            return "\n".join(body_lines).strip()
    raise PromptNotFoundError(
        "Prompt file has an opening '---' frontmatter delimiter but no "
        "closing one; refusing to load."
    )


@cache
def load_prompt(component: str) -> str:
    """Load the active prompt body for ``component`` as a single string.

    Looks up ``component`` in ``prompts/version.json``, reads the
    matching ``prompts/<version>/<component>.md`` file, strips its
    YAML frontmatter block, and returns the body. Cached, so callers
    can bind the result to a module-level constant without paying disk
    I/O every turn.

    Raises :class:`PromptNotFoundError` (NOT :class:`FileNotFoundError`)
    when the component isn't registered in ``version.json`` or the
    pinned file doesn't exist on disk.
    """
    prompts_dir = _find_prompts_dir()
    version_map = _load_version_map(prompts_dir)
    if component not in version_map:
        registered = ", ".join(sorted(version_map)) or "<none>"
        raise PromptNotFoundError(
            f"Component {component!r} is not pinned in "
            f"{prompts_dir / _VERSION_FILE_NAME}. Registered components: "
            f"{registered}."
        )
    version = version_map[component]
    prompt_path = prompts_dir / version / f"{component}.md"
    if not prompt_path.is_file():
        raise PromptNotFoundError(
            f"Pinned prompt file does not exist: {prompt_path}. Either "
            f"the version directory is missing or {component}.md was not "
            f"shipped with this version."
        )
    return _strip_frontmatter(prompt_path.read_text(encoding="utf-8"))
