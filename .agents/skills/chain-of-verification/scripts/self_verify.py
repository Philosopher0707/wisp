#!/usr/bin/env python3
"""Self-verify prompt generator for Chain-of-Verification.

Usage:
    python self_verify.py "<original_query>" "<baseline_response>" "<verification_question>" "<verification_answer>"

Output: A prompt string to send to the LLM for final verified response.
"""

import sys
import json


def generate_self_verify_prompt(
    query: str,
    response: str,
    verification_question: str,
    verification_answer: str,
) -> str:
    """Generate a CoVe Step 4 prompt: cross-check and revise."""

    trimmed_response = response.strip()[:3000]

    prompt = f"""Given the original query, baseline response, and independent verification findings, produce a **revised and factually consistent** final response.

**CRITICAL**: The verification answers come from an independent source. If they contradict the baseline response, trust the verification answers and correct the baseline.

User Query:
{query}

Baseline Response:
{trimmed_response}

Independent Verification:
Q: {verification_question}
A: {verification_answer}

Task: Produce a revised response that resolves any inconsistencies. If the verification confirms the baseline, keep it. If the verification contradicts the baseline, correct it and explain the change.

Format: Return only the revised response text.

Revised Response:"""
    return prompt


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Usage: python self_verify.py '<query>' '<response>' '<question>' '<answer>'", file=sys.stderr)
        sys.exit(1)

    query = sys.argv[1]
    response = sys.argv[2]
    question = sys.argv[3]
    answer = sys.argv[4]
    print(generate_self_verify_prompt(query, response, question, answer))
