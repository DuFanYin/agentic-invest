"""Assemble system + user messages from PROMPTS specs."""

from __future__ import annotations

from typing import Any

from src.server.prompts.templates import PROMPTS


def build_prompt(agent: str, name: str, **kwargs: Any) -> tuple[str, str]:
    """Return (system_message, user_message).

    Each spec may include:
      system — required
      schema — optional; if present, prepended to user body
      user_template — str.format(**kwargs) body after schema
    """
    spec = PROMPTS[agent][name]
    system: str = spec["system"]
    schema_raw = spec.get("schema") or ""
    user_body = spec["user_template"].format(**kwargs)
    # rstrip() only: pre-refactor prompts used """\\n...""" — .strip() removed that
    # leading newline and the final .strip() trimmed the trailing newline of the payload.
    schema_part = schema_raw.rstrip()
    if schema_part:
        user = f"{schema_part}\n\n{user_body}"
    else:
        user = user_body
    return system, user
