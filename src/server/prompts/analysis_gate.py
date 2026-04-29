"""Paragraph injected into parallel analysis prompts when synthesis follows a supplemental research batch."""


def analysis_gate_context_for_prompt(
    *, research_iteration: int = 0, retry_questions: list[str] | None = None, retry_reason: str | None = None
) -> str:
    """Short instructions so FA / macro / sentiment LLMs know when evidence is post-gate supplemental.

    `research_iteration` is the batch counter incremented by ``research_node`` after each run
    (first completed batch → 1, second → 2, …).
    """
    qs = [str(q).strip() for q in (retry_questions or []) if str(q).strip()]
    rr = (retry_reason or "none").strip() or "none"
    supplementary = research_iteration >= 2 or bool(qs)
    if not supplementary:
        return (
            "Evidence pass: **initial** — no prior quality-gate retry. "
            "Synthesize grounded claims from the evidence below."
        )
    lines = [
        "Evidence pass: **supplemental** — batch **"
        f"{research_iteration}** followed a pipeline quality gate that requested more retrieval.",
        "Prioritize themes where **new** evidence ([ev_…]) addresses the gate; "
        "if older vs newer sources conflict, say so and weight recency where appropriate.",
    ]
    if qs:
        lines.append("Gate retrieval directive(s) — primary focus when supported by evidence:")
        lines.extend(f"- {q}" for q in qs[:4])
    lines.append(f"Gate reason code: {rr}.")
    return "\n".join(lines)
