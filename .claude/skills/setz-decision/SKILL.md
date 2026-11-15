---
name: setz-decision
description: "Run a full decision analysis for an important Settleezy decision and record it in the Decision Register - objective, history, research, implications, council, options, risks, recommendation, plan, success criteria, review date. Use for \"should we…\", pricing, expansion, hiring, funding, large partnerships, or \"review past decisions\"."
---
# Decision intelligence

Use for decisions that are costly to reverse or shape the next quarter. For small choices, answer directly.

## Steps
1. **Objective.** State the decision in one sentence, what a good outcome looks like, and by when it must be made.
   If the objective is unclear, ask Mruddual one question before going further.
2. **History.** Read `Setz/Log/decision-register.md` and the relevant files in `Setz/Settleezy/`. Say what was
   decided before on this topic and what happened.
3. **Research.** Brief at most four agents in parallel, each with a precise question (typical set: `market-research`
   or `competitor-intelligence`, `revenue-intelligence` or `finance-analyst`, `data-intelligence`, and the agent
   that owns the area). If live research or data is unavailable, say so: do not fill the gap.
4. **Implications.** Financial, operational and legal. Legal points are flagged for a qualified adviser, not decided.
5. **Council.** Run `setz-council` on the question with the research attached.
6. **Options.** Two to four real options, including "do nothing" and a small test where one exists.
7. **Risks and second-order effects** per option: what it triggers with students, partners, universities, cash,
   and what becomes harder to undo.
8. **Quality gate.** Have `quality-audit` check the numbers and sources before the recommendation is shown.
9. **Recommendation.** One option, confidence (high / medium / low), what drives the confidence, and what evidence
   is missing with the cheapest way to get it. Label key statements Fact, Estimate or Hypothesis.
10. **Implementation plan.** Steps, owner, dates. Each step tagged with its permission level.
11. **Success criteria.** Two or three measurable signs, and the date they will be checked.

Mruddual decides. Setz never records a decision as made until he has said so in his own words.

## Decision Register entry (`Setz/Log/decision-register.md`, newest first)
```
## D-0000 · <short title>
- Date: · Status: Proposed | Decided | Implemented | Reviewed | Reversed
- Description:
- Background:
- Alternatives considered:
- Council contributions: (one line per voice that mattered, with dissent)
- Final decision: (his words)
- Reasoning:
- Supporting evidence: (sources with dates; Fact / Estimate / Hypothesis)
- Expected outcome:
- Responsible:
- Review date:
- Actual outcome: (filled at review)
- Lessons learned: (filled at review)
```
Give the next free ID. Add the review date to `Setz/Log/tasks.md`.

## Review mechanism
On "review past decisions", and in every weekly intelligence run: list entries whose review date has passed and
have no actual outcome. For each, gather what happened (data, mail, tracker), compare with the expected outcome,
propose "Actual outcome" and "Lessons learned", and ask Mruddual to confirm before writing them. Look for patterns
across reviewed decisions (for example, estimates that were consistently too optimistic) and say so.
