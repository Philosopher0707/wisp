---
name: chain-of-verification
description: Apply Chain-of-Verification (CoVe) methodology to reduce hallucinations and improve factual accuracy in AI-generated outputs. Use when the user asks to verify facts, check accuracy, reduce hallucinations, improve the quality of generated content, or when producing outputs that contain factual claims (lists, biographies, summaries, technical documentation, research outputs). Also use when reviewing or revising AI-generated text for correctness. Trigger on phrases like "verify this", "fact-check", "reduce hallucinations", "improve accuracy", "review for errors", or when generating multi-claim outputs.
---

# Chain-of-Verification (CoVe)

A methodology from Dhuliawala et al. (Meta AI, 2023) for reducing hallucinations by making the model deliberate on its own output. This skill adapts CoVe for practical use in coding, documentation, and analysis tasks.

## Core Principle

**LLMs answer verification questions more accurately than they generate longform text.** CoVe exploits this gap by breaking verification into independent, short-form queries that cannot condition on the original (potentially hallucinated) response.

## When to Apply CoVe

Apply CoVe when the output contains multiple factual claims and errors are costly:
- Generating lists of entities (libraries, APIs, tools, people)
- Technical documentation with version numbers, API signatures, flags
- Summaries of research or code changes
- Biographies or fact-heavy prose
- Multi-step plans with dependencies
- Code generation involving external APIs or libraries

Skip CoVe for:
- Purely creative/generative tasks (no factual claims)
- Single-fact outputs where the claim itself is the query
- Highly time-sensitive tasks where latency is critical

## The 4-Step Workflow

### Step 1: Generate Baseline Response

Produce the initial output normally. This serves as both:
- The draft to be improved
- The baseline for measuring improvement

```
User query → LLM → Baseline Response (may contain hallucinations)
```

### Step 2: Plan Verifications

Generate focused verification questions for factual claims in the baseline. Each question should:
- Target a single, checkable fact
- Be phrased as an open question (not yes/no)
- Be answerable independently (without the baseline context)

**Example:**
> Baseline: " FastAPI 0.95.0 added the ` lifespan ` parameter for startup/shutdown events."
>
> Verification questions:
> - "What version of FastAPI introduced the `lifespan` parameter?"
> - "What is the `lifespan` parameter in FastAPI used for?"

**Anti-pattern:** Yes/no questions ("Did FastAPI 0.95.0 add the lifespan parameter?") — models tend to agree with embedded facts regardless of correctness.

### Step 3: Execute Verifications (Factored)

Answer each verification question in a **separate, independent prompt** that does NOT include:
- The baseline response
- Answers to other verification questions
- Any context from previous reasoning

This is the critical insight from the paper: when verification answers condition on the baseline, the model repeats hallucinations.

**Execution variants (choose based on latency/cost):**

| Variant | Method | Accuracy | Latency | Cost |
|---------|--------|----------|---------|------|
| Joint | Single prompt with all Q&A | Lowest | Fastest | Lowest |
| 2-Step | Separate planning & answering | Medium | Medium | Medium |
| **Factored** | **Independent prompt per question** | **Highest** | **Slowest** | **Highest** |
| Factor+Revise | Factored + explicit cross-check | Highest | Slowest | Highest |

**Recommendation:** Default to **Factored**. For batch operations, run verifications in parallel if the LLM client supports it.

### Step 4: Generate Final Verified Response

Synthesize the baseline with verification results:

```
Original claims + Verification answers → Cross-check for inconsistencies → Revised output
```

Explicitly disregard claims contradicted by verification. Preserve claims confirmed by verification. For uncertain claims, downgrade confidence or omit.

**Factor+Revise variant:** Add an explicit cross-check step where each claim is labeled:
- `CONSISTENT` — verification confirms
- `INCONSISTENT` — verification contradicts
- `PARTIALLY CONSISTENT` — partially confirmed

Then generate the final response using only CONSISTENT claims.

## Implementation in Wisp

### As a Skill Prompt

When generating outputs with this skill active, the Wisp agent should:

1. Produce the baseline response
2. Identify factual claims (entities, versions, dates, names, technical facts)
3. Generate 1-3 verification questions per claim block
4. Spawn independent subagent calls for each verification (see references/subagent-pattern.md)
5. Cross-check and revise

### As a Tool

Consider adding a `verify_facts` tool that:
- Takes a text block as input
- Extracts claims automatically (or with LLM prompt)
- Runs factored verification
- Returns the revised text with inconsistency annotations

## Verification Question Templates

For **technical/code outputs**:
- "What is the signature of `[function]` in `[library]` version `[version]`?"
- "When was `[feature]` introduced in `[tool]`?"
- "What is the correct syntax for `[API call]` in `[language]`?"

For **entity lists**:
- "Who is `[entity]` and what are they known for?"
- "What is `[entity]`'s relationship to `[domain]`?"
- "When/where was `[entity]` established?"

For **summaries**:
- "What did `[source]` say about `[topic]`?"
- "When did `[event]` occur?"
- "Who was responsible for `[action]`?"

## Key Results (from Paper)

- **Wikidata list precision:** 0.17 → 0.36 (factored CoVe), hallucinations down 77%
- **MultiSpanQA F1:** 0.39 → 0.48 (+23%)
- **Biography FACTSCORE:** 55.9 → 71.4 (factor+revise), beating ChatGPT (58.7) and PerplexityAI (61.6)
- **Verification accuracy:** ~70% correct when queried individually vs ~17% in longform baseline

## References

- Full paper analysis: [references/paper-analysis.md](references/paper-analysis.md)
- Subagent verification pattern: [references/subagent-pattern.md](references/subagent-pattern.md)
- Prompt templates: See Step 2 examples above
