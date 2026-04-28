"""Centralized LLM prompts — strings in templates.py; assembly in builder.py."""

from src.server.prompts.builder import build_prompt
from src.server.prompts.templates import PROMPTS

__all__ = ["PROMPTS", "build_prompt"]
