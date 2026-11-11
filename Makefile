# analytics-agent: one-command targets. All LLM responses are cached under .llm_cache/,
# so re-running any eval target after the first run is free and deterministic.
UV ?= uv
PY = $(UV) run python

.PHONY: init check verify pg-start pg-stop pg-load pg-verify corpus index eval-v2 redteam-v2 lag-experiment judge-v2 site pack-v2 unpack-v2 unpack pack reproduce-results setup data bird olist verify-data retrieval eval-small eval-full eval-qwen semantic ambiguity \
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
	$(PY) -m analytics_agent.llm.ledger

gate:             ## CI regression gate (replay only, no network)
	$(UV) run pytest -q tests/test_regression_gate.py

record-regression: ## re-record the gate's cassette with the real model after a prompt change
	$(PY) -m analytics_agent.eval.regression record

test:
	$(UV) run pytest -q

lint:
	$(UV) run ruff check .

all reproduce-v1: setup data verify-data retrieval redteam eval-full semantic ambiguity abstention routing frontier traces report test lint

clean-runs:       ## delete run outputs (NOT the LLM cache)
	rm -f results/runs/*.jsonl traces/*.jsonl

# ------------------------------------------------------------------ v2 copilot
PGBIN ?= /opt/homebrew/opt/postgresql@16/bin
N ?= 15          ## questions per half (15 author + 15 codex-blind); N=all for the full 60+60
RT_N ?=          ## red-team scenarios per set (empty = all 20+20)

init:             ## pinned deps + committed replay caches
	$(UV) sync --locked
	$(MAKE) unpack-v2

unpack-v2:
	mkdir -p cache/v2_llm && tar -xzf cache/v2_llm.tar.gz -C cache

pack-v2:          ## after live runs: refresh the committed LLM replay cache
	tar -czf cache/v2_llm.tar.gz -C cache v2_llm

check:            ## OFFLINE: lint + unit/permission tests + eval gate (replay backend, zero LLM calls)
	$(UV) run ruff check src tests scripts/v2
	AA_LLM_BACKEND=replay AA_MAX_CLAUDE_CALLS=0 $(UV) run pytest -q tests/v2
	AA_LLM_BACKEND=replay AA_MAX_CLAUDE_CALLS=0 AA_MODEL=mock $(UV) run pytest -q tests --ignore=tests/v2 --ignore=tests/test_regression_gate.py
	AA_MAX_CLAUDE_CALLS=0 $(UV) run pytest -q tests/test_regression_gate.py

verify:           ## run every feature_list.json verification command and record PASS/FAIL
	$(UV) run python scripts/verify_features.py

pg-start:
	mkdir -p data/pg && ( [ -d data/pg/cluster ] || $(PGBIN)/initdb -D data/pg/cluster -U postgres --auth=trust -E UTF8 --locale=en_US.UTF-8 )
	LC_ALL=en_US.UTF-8 $(PGBIN)/pg_ctl -D data/pg/cluster -l data/pg/server.log -o "-p 5433 -c shared_buffers=256MB" start

pg-stop:
	$(PGBIN)/pg_ctl -D data/pg/cluster stop

pg-load:          ## 311 window (2024-11-01..2026-11-03) from ~/projects/chicago-311-warehouse (read-only) + RLS
	$(PY) scripts/v2/pg_load.py

pg-verify:
	$(PY) scripts/v2/pg_verify.py

corpus:           ## polite fetch of City of Chicago documents (cached)
	$(PY) scripts/v2/fetch_corpus.py

index:            ## chunk + embed the corpus (bge-small via fastembed), refresh cache/embeddings.npz
	$(PY) -c "from analytics_agent.copilot.copilot import build_deployment as b; d=b(); d.index.embedder.cache.save()"

eval-v2:          ## live: copilot + baselines + judge on N per half, then score
	AA_EVAL_N=$(N) $(PY) -m analytics_agent.eval.copilot_eval run --system copilot
	AA_EVAL_N=$(N) $(PY) -m analytics_agent.eval.copilot_eval run --system docs_only
	AA_EVAL_N=$(N) $(PY) -m analytics_agent.eval.copilot_eval run --system v1_sql
	$(MAKE) judge-v2 N=$(N)

judge-v2:
	for s in copilot docs_only v1_sql; do AA_EVAL_N=$(N) $(PY) -m analytics_agent.eval.copilot_eval judge --system $$s; done
	AA_EVAL_N=$(N) $(PY) -m analytics_agent.eval.copilot_eval score

redteam-v2:       ## live red team (sequential; shares the Postgres role table)
	$(PY) -m analytics_agent.eval.redteam_v2 --round final $(if $(RT_N),--n $(RT_N),)

lag-experiment:
	$(PY) scripts/v2/lag_experiment.py

site:             ## static demo page from recorded runs + Playwright screenshots
	$(PY) scripts/v2/build_site.py
	node scripts/v2/screenshot.cjs   # needs the playwright npm package (set NODE_PATH to its node_modules)

reproduce: pg-load corpus index eval-v2 redteam-v2 lag-experiment site   ## live v2 path end to end
