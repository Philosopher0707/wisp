#!/usr/bin/env python3
"""Self-critique prompt generator for Chain-of-Verification.

Usage:
    python self_critique.py "<original_query>" "<baseline_response>"

Output: A prompt string to send to the LLM for generating verification questions.
"""

import sys
import textwrap


def generate_self_critique_prompt(query: str, response: str) -> str:
    """Generate a CoVe Step 2 prompt: plan verification questions."""
    trimmed_response = response.strip()[:4000]

    # Use factored template to prevent conditioning on hallucinations
    prompt = f"""Given the following user query and baseline response, generate **independent verification questions** that fact-check each concrete claim in the response.

**Independence rule**: Pretend you do NOT have the baseline response. Generate questions that any neutral fact-checker would ask about the topic.

User Query:
{query}

Baseline Response:
{trimmed_response}

Generate up to 5 verification questions. Each question should be an open-ended, factual query (not yes/no). Do not reference the baseline response in the questions.

Format: Return only a numbered list, one question per line.

Verification Questions:"""
    return prompt


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python self_critique.py '<query>' '<response>'", file=sys.stderr)
        sys.exit(1)

    query = sys.argv[1]
    response = sys.argv[2]
    print(generate_self_critique_prompt(query, response))
