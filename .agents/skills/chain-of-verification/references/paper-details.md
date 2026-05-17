# Chain-of-Verification Reference

## Paper: Chain-of-Verification Reduces Hallucination in Large Language Models
**Dhuliawala et al. (Meta AI & ETH Zürich), arXiv:2309.11495, September 2023**

---

## The Core Pipeline

1. **Generate Baseline Response** → Draft answer to user query
2. **Plan Verifications** → Generate verification questions for each factual claim
3. **Execute Verifications** → Answer verification questions independently
4. **Generate Final Response** → Revise baseline using verification results

---

## Verification Question Design

### What Makes Good Verification Questions?

| Quality | Description |
|---------|-------------|
| **Specific** | Target individual facts, not broad claims |
| **Open-ended** | "When did X happen?" performs better than yes/no |
| **Independent** | Each question can be answered without context from others |
| **Falsifiable** | Has a verifiable correct answer |

### Open Questions Beat Yes/No

From Table 4 in the paper (Wiki-Category task):

| Question Type | Precision |
|---------------|-----------|
| Rule-based yes/no | 0.16 |
| Model-generated yes/no | 0.19 |
| **Model-generated open questions** | **0.22** |

Why? Yes/no format causes the model to **agree with embedded facts** whether right or wrong. Open-ended questions force retrieval of correct fact from parametric memory.

---

## Execution Variants

### Variant Comparison

| Variant | Context for Verification Answers | Key Feature | When to Use |
|---------|-----------------------------------|-------------|-------------|
| **Joint** | Sees baseline response | Single prompt | Fastest, but prone to repetition |
| **2-Step** | Does NOT see baseline | Separate planning/execution | Better balance |
| **Factored** | Does NOT see baseline, one prompt per question | Maximum independence | Best accuracy, parallelizable |
| **Factor+Revise** | Factored + explicit cross-check | Explicit inconsistency detection | Most robust, especially longform |

### Why Removing Baseline Context Matters

The paper's key insight: **Models repeat hallucinations when verification sees the original response.**

From the paper: "In the joint method... the verification answers have to condition on the initial response as well. This may increase the likelihood of repetition... the verification questions might hallucinate similarly to the original baseline response."

---

## Prompt Templates

### Longform Generation (Biographies) — Factor+Revise

**Step 1: Generate Baseline**
```
Q: Tell me a bio of <person>
A: <bio of person>
[3 few-shot examples]
Q: Tell me a bio of <target>
A:
```

**Step 2: Plan Verifications**
```
Context: Q: Tell me a bio of <person>.
A: <passage about person>
Response:
<fact>, Verification Question
<fact>, Verification Question
[3 few-shot examples]
Context: Q: Tell me a bio of <target>.
A: <baseline response>
Response:
```

**Step 3: Execute Verifications**
```
Q: Verification Question
A: Answer
[3 few-shot examples]
Q: <target verification question>
A:
```

**Step 4: Cross-Check (Factor+Revise)**
```
Context: <Original Fact>
From another source,
Q: <verification question>
A: <verification answer>
Response: CONSISTENT. <consistent fact>
[or]
Response: INCONSISTENT.
[3 few-shot examples]
```

**Step 5: Final Verified Response**
```
Context: <Original Passage>
From another source,
Q: <question>
A: <answer>
Q: <question>
A: <answer>
Response: <revised passage>
[3 few-shot examples]
```

---

## Key Experimental Results

### Wikidata List Generation
- Baseline Llama 65B: Precision 0.17 (0.59 correct, 2.95 hallucinated)
- CoVe (Factored): Precision 0.36 (0.38 correct, 0.68 hallucinated)
- **→ Hallucinations drop 77%, correct answers drop only 36%**

### MultiSpanQA (closed-book QA)
- Baseline F1: 0.39
- CoVe (Factored) F1: 0.48 (+23%)

### Longform Biography Generation (FACTSCORE)
| Model | FACTSCORE | Avg Facts |
|-------|-----------|-----------|
| Llama 65B few-shot | 55.9 | 16.6 |
| Llama 65B CoVe (joint) | 60.8 | 12.8 |
| Llama 65B CoVe (factored) | 63.7 | 11.7 |
| **Llama 65B CoVe (factor+revise)** | **71.4** | 12.3 |
| ChatGPT (zero-shot) | 58.7 | 34.7 |
| PerplexityAI (retrieval-based) | 61.6 | 40.8 |

**→ CoVe beats ChatGPT and PerplexityAI on longform generation.**

### Key Finding: Verification > Baseline
On Wikidata, only ~17% of baseline entities are correct, but **~70% are correct when queried individually via verification questions.** This gap is the core opportunity.

---

## Integration Architecture

### Mapping CoVe to Agent Systems

| CoVe Component | Typical Implementation |
|----------------|----------------------|
| Baseline generation | Agent's normal response pipeline |
| Verification planning | Separate prompt/tool call |
| Verification execution | Parallel subagent calls (one per question) |
| Cross-check | Post-processing comparison step |
| Final revision | Final prompt with verified facts |

### Parallelization Strategy

The **Factored** variant is naturally parallel — each verification question can be answered in a separate subagent/subprocess simultaneously. The paper notes: "While this is potentially more computationally expensive, requiring the execution of many more LLM prompts, they can be run in parallel, and hence be batched."

### When NOT to Use CoVe

1. **Simple factual lookup** where you already have a source of truth (use RAG instead)
2. **Creative writing** where there IS no single correct answer
3. **Real-time latency-critical applications** (adds 3-5x latency)
4. **When the model fundamentally lacks the knowledge** (CoVe can't fix unknown unknowns)

---

## Limitations from the Paper

1. Still hallucinates — doesn't eliminate all errors
2. Only addresses stated factual inaccuracies, not reasoning errors or opinions
3. Computational cost: more tokens generated (~3-5x, similar to CoT)
4. Upper bound is model's own knowledge — can't know what it doesn't know
5. No tool use explored — combining with RAG would likely improve further
