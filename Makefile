# analytics-agent: one-command targets. All LLM responses are cached under .llm_cache/,
# so re-running any eval target after the first run is free and deterministic.
UV ?= uv
PY = $(UV) run python

.PHONY: unpack pack reproduce-results setup data bird olist verify-data retrieval eval-small eval-full eval-qwen semantic ambiguity \
        abstention routing frontier redteam traces report test lint gate record-regression all reproduce clean-runs

setup:            ## create the py3.12 venv with pinned deps (uv.lock)
	$(UV) sync --extra embed

unpack:           ## fresh clone: restore recorded run files and the LLM response cache (no model calls needed)
	for f in results/runs/*.jsonl.gz; do gunzip -kf "$$f"; done
	shasum -a 256 -c cache/llm_cache.tar.gz.sha256 && tar -xzf cache/llm_cache.tar.gz

pack:             ## after new runs: refresh the committed, compressed copies
	for f in results/runs/*.jsonl; do gzip -9 -c "$$f" > "$$f.gz"; done
	tar -czf cache/llm_cache.tar.gz .llm_cache && shasum -a 256 cache/llm_cache.tar.gz > cache/llm_cache.tar.gz.sha256

reproduce-results: unpack ## regenerate every results/* file and chart from recorded runs; fails on any cache miss
	AA_STRICT_REPLAY=1 AA_MAX_CLAUDE_CALLS=0 $(PY) scripts/reproduce_results.py
	git diff --stat -- results docs/img

data: bird olist  ## download + build all data (BIRD Mini-Dev SQLite DBs, Olist DuckDB warehouse)

bird:
	bash scripts/download_bird.sh
	$(PY) scripts/build_fewshot_pool.py
	$(PY) scripts/make_subsets.py

olist:
	bash scripts/download_olist.sh
	$(PY) scripts/build_olist.py
	$(PY) scripts/build_olist.py --redteam

verify-data:      ## A1/A3: every gold query runs
	$(PY) scripts/verify_bird_gold.py
	$(PY) scripts/verify_olist_sets.py

retrieval:        ## table-retrieval recall (no LLM)
	$(PY) -m analytics_agent.eval.retrieval_eval

eval-small:       ## 20-question smoke eval (Haiku, full pipeline)
	$(PY) scripts/run_evals.py --plan small

eval-full:        ## all BIRD runs in the README (Haiku chain on 500, then Sonnet/a2/SC on sub200)
	$(PY) scripts/run_evals.py --plan full
	$(PY) scripts/run_evals.py --plan sub

eval-qwen:        ## local qwen2.5:7b via Ollama (slow: ~1 min/question)
	$(PY) scripts/run_evals.py --plan qwen

semantic:         ## Olist: raw SQL vs glossary vs governed semantic layer
	$(PY) -m analytics_agent.eval.olist_experiments semantic

ambiguity:        ## Olist: clarification on ambiguous questions vs clear controls
	$(PY) -m analytics_agent.eval.olist_experiments ambiguity

abstention:       ## risk-coverage curves (BIRD sub200 SC x5 from eval-full; Olist raw arm SC x5) + noise floor
	$(PY) -m analytics_agent.eval.run --suite olist --config olist_raw --n-samples 5 --workers 1
	$(PY) -m analytics_agent.eval.analysis abstention
	$(PY) scripts/noise_floor.py

routing:
	$(PY) -m analytics_agent.eval.analysis routing

frontier:
	$(PY) -m analytics_agent.eval.analysis frontier

redteam:          ## guardrail red-team suite (SQL layer, bypass layer, LLM prompt-injection layer)
	$(PY) -m analytics_agent.eval.redteam

traces:
	$(PY) -m analytics_agent.trace_report

report:
	$(PY) -m analytics_agent.eval.analysis ablations
	$(PY) scripts/error_analysis.py
	$(PY) scripts/compare_sister_metrics.py
	$(PY) -m analytics_agent.llm.ledger

gate:             ## CI regression gate (replay only, no network)
	$(UV) run pytest -q tests/test_regression_gate.py

record-regression: ## re-record the gate's cassette with the real model after a prompt change
	$(PY) -m analytics_agent.eval.regression record

test:
	$(UV) run pytest -q

lint:
	$(UV) run ruff check .

all reproduce: setup data verify-data retrieval redteam eval-full semantic ambiguity abstention routing frontier traces report test lint

clean-runs:       ## delete run outputs (NOT the LLM cache)
	rm -f results/runs/*.jsonl traces/*.jsonl
