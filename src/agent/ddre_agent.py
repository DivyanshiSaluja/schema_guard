"""LLM repair loop driven by drift events and reference-path witnesses.

Unlike repair_agent.py, the prompt carries the confirmed drift events rather
than a raw diff, and a rejected attempt is answered with the minimal,
redacted witness from the comparator instead of a stack trace. No source
rows other than the witness are ever sent to the model.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict

from src.reference.drift import DriftEvent
from src.reference.verify import VerificationResult


SYSTEM_PROMPT = """You repair a Python ETL transformation after a source schema change.
Return ONLY a Python function `def transform(row):` that returns a dict literal.
Allowed: subscripts like row['col'], + - * / %, comparisons, `x if c else y`,
the calls bool, float, int, len, str and the string methods split, strip, lower,
upper, title, replace. No imports, loops, statements other than return,
or markdown fences. The output fields must stay the same as the current code."""


def build_prompt(events: tuple[DriftEvent, ...], incumbent_code: str, feedback: str | None) -> str:
    changes = json.dumps(
        [{k: v for k, v in asdict(e).items() if v not in (None, (), 0)} for e in events],
        indent=2,
    )
    prompt = f"Confirmed schema changes:\n{changes}\n\nCurrent transformation:\n{incumbent_code}"
    if feedback:
        prompt += f"\n\nYour previous attempt was rejected. Minimal failing example:\n{feedback}\nFix it."
    return prompt


def witness_feedback(result: VerificationResult) -> str:
    w = result.witness
    if w is None:
        return f"verdict {result.verdict}; invariant violations {sorted(result.invariant_violations)}"
    diff = w.read_difference
    return json.dumps({
        "input_rows": list(w.rows),
        "failure": {"field": w.signature[0], "kind": w.signature[1]},
        "fields_read_only_by_you": sorted(diff["candidate_only"]),
        "fields_the_reference_reads": sorted(w.reference_reads),
    }, default=str)


def call_model(model: str, prompt: str) -> str:
    from openai import OpenAI

    client = OpenAI(
        base_url=os.getenv("LLM_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        api_key=os.getenv("NVIDIA_API_KEY"),
    )
    response = client.chat.completions.create(
        model=model,
        max_tokens=600,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
    )
    text = response.choices[0].message.content.strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("python").strip()
    return text


def repair(events, incumbent_code, verify, *, model: str, attempts: int = 3):
    """Ask the model for a candidate until ``verify(code)`` is deployable.

    Returns the list of (code, VerificationResult) for every attempt.
    """

    history = []
    feedback = None
    for _ in range(attempts):
        code = call_model(model, build_prompt(events, incumbent_code, feedback))
        result = verify(code)
        history.append((code, result))
        if result.deployable or result.needs_waiver:
            break
        feedback = witness_feedback(result)
    return history
