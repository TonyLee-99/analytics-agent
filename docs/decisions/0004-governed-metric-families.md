# 0004. Conflicting metric definitions are explicit families; the copilot asks

**Decision.** Reuse v1's governed metric compiler; a metric may belong to a family with several governed
variants (owner, selector phrases). If the question names no variant, the copilot asks; selector phrases
are matched deterministically in the question text, not judged by the model.

**Rejected.** Letting the model pick a definition (v1 measured silent wrong-definition answers on 8/30
metric questions with raw SQL), or picking the asker's department default silently.

**Measured reason.** v1: governed definitions took metric questions from 14/30 to 27/30 (docs/v1). v2:
clarification rate on ambiguous conflict questions and false clarifications on explicit ones are in
results/v2/eval.json.
