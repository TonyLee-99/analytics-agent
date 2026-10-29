---
license: cc-by-sa-4.0
task_categories:
- table-question-answering
- question-answering
language:
- en
size_categories:
- 1K<n<10K
configs:
- config_name: default
  data_files:
  - split: mini_dev_sqlite
    path: data/mini_dev_sqlite.jsonl
  - split: mini_dev_mysql
    path: data/mini_dev_mysql.jsonl
  - split: mini_dev_pg
    path: data/mini_dev_postgresql.jsonl
---

# BIRD SQL Mini-Dev

## 2026 update

This update provides 500 aligned question IDs for SQLite, MySQL and
PostgreSQL. It revises the annotations from the last update after community
feedback and a systematic, human-in-the-loop review of questions, evidence
and gold SQL.

Our aim is a benchmark that requires an agent to understand the request,
explore the permitted database and construct the query. We clarified
materially ambiguous output contracts, corrected reference SQL and replaced
selected instances, while removing evidence that supplied unnecessary schema
links, query plans or answers. We carefully considered each suggestion
against the question's intended meaning and the underlying database, and
incorporated changes where the evidence supported them.

## Moving forward

**BIRD 1.0** achieved broad adoption and helped shape text-to-SQL research
and industry development by establishing standardized task formats and
realistic database environments, unlocking technological advances in **RAG,
multi-agent systems, and knowledge generation**. We are now developing BIRD 2.0, focusing on more realistic database development and enterprise scenarios. It retains text-to-SQL while introducing more complex agentic tasks, covering not only data querying, but also developing, testing, and maintaining data workflows. Our goal is to better evaluate frontier models' data intelligence and reliability in these settings.

Follow the [BIRD website](https://bird-bench.github.io/) for updates on our
next-generation benchmarks: **LiveSQLBench 2.0**, **BIRD 2.0**, and **DII
v1.0**.

**All contributions, real-world samples, and suggestions of any kind are
welcome; get in touch with the BIRD Team ([bird.bench25@gmail.com](mailto:bird.bench25@gmail.com?subject=BIRD%20Projects%20Contribution%20%26%20Collaboration))
to collaborate.**

We sincerely thank **OpenAI**, **Amazon**, and **Google Cloud** for their early
support and collaboration.

## Repository layout

| Directory | Contents |
| --- | --- |
| [`data/`](data/) | Current annotations for all three SQL dialects |
| [`baseline_inference/`](baseline_inference/) | Ready-to-use baseline prompts and supporting schemas |
| [`archive/`](archive/) | Historical 2025 release |

Interactive tasks are hosted separately on
[Harbor Hub](https://hub.harborframework.com/datasets/birdsql/bird-mini-dev).

## Load the current data

```python
from datasets import load_dataset

mini_dev = load_dataset("birdsql/bird_mini_dev")
print({name: len(split) for name, split in mini_dev.items()})
# {'mini_dev_sqlite': 500, 'mini_dev_mysql': 500, 'mini_dev_pg': 500}
```

Each split contains 500 instances, IDs 0–499. Question and evidence text
match across dialects; reference SQL is dialect-specific. Match records by
`question_id` and dialect.

## Baseline results

Fixed-prompt baseline execution accuracy (EX, %) on 500 questions per dialect.
Model rows are sorted by Overall EX, from highest to lowest.

| Model | SQLite | MySQL | PostgreSQL | Overall |
| --- | ---: | ---: | ---: | ---: |
| **Empirical Upper Bound** | **99.60** | **93.80** | **97.00** | **96.80** |
| GPT-6.1 Sol | 88.00 | 84.80 | 89.00 | 87.27 |
| Claude Opus 5.5 | 90.00 | 84.40 | 87.00 | 87.13 |
| Claude Sonnet 5.5 | 86.60 | 80.40 | 85.60 | 84.20 |
| Qwen3.8-Flash-Next | 70.20 | 67.00 | 67.80 | 68.33 |
| DeepSeek-V4.1-Flash | 61.00 | 55.80 | 62.20 | 59.67 |
| Claude Haiku 5.5 | 59.60 | 55.20 | 58.60 | 57.80 |
| GPT-6 Luna | 54.00 | 51.00 | 51.80 | 52.27 |
| GLM-5.3 | 51.40 | 48.40 | 48.40 | 49.40 |
| Qwen3.6-35B-A3B | 21.20 | 22.20 | 20.00 | 21.13 |

**Overall** is EX across all 1,500 dialect-specific answers, equivalent to the
mean of the three dialect scores. **Empirical Upper Bound**
counts a question as correct within each dialect if at least one evaluated
model answers it correctly.

## Baseline and Harbor

- **Baseline:** use each row's `prompt` in `baseline_inference/` as model
  input. The separate gold `SQL` field is for evaluation only. See the
  [baseline guide](baseline_inference/README.md).
- **Harbor:** use [BIRD Mini-Dev on Harbor Hub](https://hub.harborframework.com/datasets/birdsql/bird-mini-dev)
  for interactive evaluation. Submit the final query to `/app/answer.sql`.
  The Harbor dataset includes the local isolation provider; solver and verifier
  environments have network access disabled.

Database images are available for
[SQLite](https://hub.docker.com/r/3036197201/bird-mini-dev-sqlite),
[MySQL](https://hub.docker.com/r/3036197201/bird-mini-dev-mysql), and
[PostgreSQL](https://hub.docker.com/r/3036197201/bird-mini-dev-postgresql),
with amd64 and arm64 support.

## Acknowledgements

Thank you to the BIRD community for careful reports on question wording,
evidence, gold SQL and database consistency. We retain our thanks to the
[NVIDIA researchers](https://github.com/AlibabaResearch/DAMO-ConvAI/issues/234)
and [Thinking Machines Lab](https://thinkingmachines.ai/news/putting-task-expertise-into-rl/)
for their detailed benchmark reports and discussions, and to GitHub community
reporters [@ivermin1123](https://github.com/ivermin1123),
[@christian-gheorghe](https://github.com/christian-gheorghe),
[@headinthebox](https://github.com/headinthebox),
[@tejusarora](https://github.com/tejusarora),
[@gkorland](https://github.com/gkorland),
[@tohpinren](https://github.com/tohpinren), and
[@MichaelBruwerSwartz](https://github.com/MichaelBruwerSwartz).

## Previous release and citation

The original 2025-07-04 release remains in
[`archive/bird_mini_dev_2025-07-04/`](archive/bird_mini_dev_2025-07-04/).
Its annotations and baseline tables are historical, not current results.

```bibtex
@article{li2024can,
  title={Can llm already serve as a database interface? a big bench for large-scale database grounded text-to-sqls},
  author={Li, Jinyang and Hui, Binyuan and Qu, Ge and Yang, Jiaxi and Li, Binhua and Li, Bowen and Wang, Bailin and Qin, Bowen and Geng, Ruiying and Huo, Nan and others},
  journal={Advances in Neural Information Processing Systems},
  volume={36},
  year={2024}
}
```
