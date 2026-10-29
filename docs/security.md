# Security model and red-team results

The threat model has three parts:
1. an LLM that writes the wrong kind of SQL,
2. a user who tries to make it do so,
3. **data values** that try to make it do so (prompt injection through the warehouse itself).

The design rule is that no single layer is trusted. Each layer is tested on its own with the layer
above it switched off. Tests cover integrity (nothing changes), confidentiality (nothing leaks) and
availability (nothing exhausts the host).

| Layer | Where | What it stops |
|---|---|---|
| 1. Prompt rules | `prompts.SYSTEM_AGENT` | Asks for SELECT only and says to treat question and data text as data. Assumed bypassable. |
| 2. AST validator | `guardrails.validate_sql` (sqlglot) | Exactly one statement. Read-only root, with no DML, DDL or command node anywhere in the tree. Deny-listed file, extension and host functions. A table allowlist with **scope-aware CTE resolution**; system catalogs are rejected on every table node. Size-amplifying functions must take small literal sizes. LIMIT is injected or clamped, including `LIMIT -1`. |
| 3. Connection | `executor.connect_readonly` | **SQLite:** `mode=ro`, `query_only`, an authorizer that allows only SELECT/READ/FUNCTION, and `SQLITE_LIMIT_LENGTH` of 1 MB per value. **DuckDB:** `read_only`, no external access, extension autoload off, `memory_limit=2GB`, configuration locked. |
| 4. Data minimization | `scripts/build_olist.py` | The file the agent opens holds only masked, materialized tables. Zip-code prefixes are **keyed HMAC-SHA256 tokens**: the key lives outside the warehouse, so joins still work but tokens cannot be inverted. Lat/lng are rounded to about 11 km. |
| 5. Resource limits | `executor.execute` | Statement timeout (SQLite progress handler; DuckDB `interrupt()`). Hard row cap. **Results are fetched in chunks against a 20 MB byte budget.** |

## Holes found by red-teaming, and fixed

| # | Found by | Hole | Fix | Regression test |
|---|---|---|---|---|
| 1 | Bypass test (validator off) | SQLite `ATTACH` and `VACUUM INTO` created new files on a `mode=ro` connection | SQLite authorizer | `test_sqlite_connection_is_read_only_and_cannot_attach` |
| 2 | Bypass test (validator off) | Masking was done with views over a raw schema in the same file, so `raw.customers` was readable | Materialized masked tables; raw schema dropped | `bypass_layer` in `make redteam` |
| 3 | **External review** | **Zip tokens were `'zip_' \|\| substr(md5(zip),1,10)`: unsalted MD5 over only 100,000 possible values.** One read-only recursive-CTE SELECT, which passed the validator, enumerated every prefix and **re-identified all 99,441 customers in 0.4 s** | Keyed HMAC tokens computed in Python; the key never enters the DB | `test_zip_dictionary_attack_reidentifies_nobody` (v1 replica: 99,441/99,441 re-identified; current tokens: 0) |
| 4 | External review | **CTE shadowing:** `WITH sqlite_master AS (SELECT 1) SELECT sql FROM main.sqlite_master` passed, because any table whose bare name matched *any* CTE anywhere was exempt. The same worked for `information_schema` and `pg_catalog`, leaking DDL and configuration | Per-scope CTE resolution. Qualified names are never exempt. A non-recursive self-reference counts as a base table. System names are rejected on every node and as CTE names | `test_cte_shadowing_cannot_hide_tables` (6 cases) |
| 5 | External review | **Memory DoS:** the row cap bounded rows, not bytes. `SELECT zeroblob(500000) … LIMIT 1000` returned 500 MB, and `printf('%.*c', 9e8, 'x')` allocated about 1 GB in one VM step that the timeout cannot interrupt | Validator size policy; SQLite `SQLITE_LIMIT_LENGTH`; DuckDB `memory_limit`; 20 MB fetch budget | `test_memory_bombs_rejected_by_validator`, `test_memory_bombs_bounded_without_validator` |

Holes 3–5 mean that v1's "53/53 attacks blocked, 0 PII leaks" was true of the attacks I had written,
not of the system. The original suite tested integrity only. `scripts/redteam_v1_check.py` runs the
current 64 attacks through the v1 validator: **11 of them pass**, which are the 6 CTE-shadowing cases
and the 5 memory bombs.

## Red-team results now (`make redteam` → `results/redteam.json`)
| Layer | Result |
|---|---|
| SQL (validator) | 64/64 attacks blocked. Covers DDL, DML, stacked statements, DELETE in a CTE, ATTACH, COPY/EXPORT, extensions, file and table functions, system catalogs, CTE shadowing and memory bombs. 0 false positives on the 550 gold queries and on 8 benign look-alikes. |
| Recorded agent SQL | Of the 3,265 queries the agent actually produced in all runs, the hardened validator rejects 20. All 20 were already wrong (parse errors, invented tables). **0 previously-correct answers are lost** (`scripts/validator_recorded_fp.py`). |
| Integrity (validator off, DB copies) | Both databases unchanged, no stray files. Residual: DuckDB `CREATE TEMP VIEW` runs (in memory, gone on close). |
| Availability (validator off) | 1,000 × `zeroblob(500000)` and 1,000 × `repeat('x', 500000)` stop with "result exceeds 20 MB". `printf('%.*c', 9e8)` returns NULL because SQLite refuses to build the string. |
| Confidentiality | The zip dictionary attack (md5/sha1/sha256, 10- or 16-character prefixes) re-identifies **0** customers. The same attack on a v1 replica re-identifies **99,441/99,441**. |
| Prompt injection (LLM, recorded against v1 data) | 15 cases, 3 of them with payloads planted in data values. DB unchanged; 0 raw zips returned; 0 system-prompt leaks. The model attempted an unsafe statement once, and the validator blocked it. |

## Residual risks and what production adds
- **The 20 MB cap is a Python-side fence.** It stops the agent from receiving or holding a huge result,
  but the engine can still build intermediate results up to DuckDB's `memory_limit` (2 GB) before
  the fetch budget trips; SQLite is bounded per value by `SQLITE_LIMIT_LENGTH`, not per query.
- **No memory-limited subprocess.** macOS does not enforce `RLIMIT_AS`, so it would be theatre here.
  The engine limits plus the fetch budget are the inner fence. In a container, the cgroup memory
  limit is the outer fence.
- **Semantic exfiltration.** A user allowed to read a table can page through all of it
  (`LIMIT 1000 OFFSET k`). Entitlements and per-user rate limits decide that, not row caps.
  Production runs queries *as the end user* (`docs/production_readiness.md`).
- **Deny-list, not allow-list, for functions.** A new file or network function in a future engine
  version would not be blocked by layer 2. DuckDB's disabled external access is the backstop.
- **Version functions** (`sqlite_version()`, `version()`) are allowed. They are a low-impact
  information leak.
- **Free text and summaries.** Free-text columns such as review comments can contain customer-written
  PII; they are only flagged in the catalog. Data values reach the summarizer prompt. The summary is
  display-only, and the SQL and result table are always shown next to it.
- **Quasi-identifiers.** A keyed token stops inversion, but zip prefix + city + timestamp can still
  narrow down a person. Production would check k-anonymity of the exposed column combinations.
