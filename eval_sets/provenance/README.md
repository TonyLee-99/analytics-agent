# Provenance of the published BIRD baseline numbers

- Source: the Hugging Face dataset card of `birdsql/bird_mini_dev`, saved here as
  `bird_mini_dev_card@5b256b5.md` (dataset revision `5b256b5a9c17478f06ed6912612e9a14d17065c8`,
  2026-10-27). Section "Baseline results", table "Fixed-prompt baseline execution accuracy (EX, %) on
  500 questions per dialect": Claude Haiku 5.5 SQLite **59.60**, Claude Sonnet 5.5 SQLite **86.60**.
- The baseline table and the reviewed annotations were released in the same commit
  (`38818fcbc460`, 2026-10-26, "Release reviewed Mini-Dev update with archived 2025 annotations and
  baseline inputs"). The `data/` files are byte-identical between that commit and `5b256b5`
  (same git blob ids).
- Our local `mini_dev_sqlite.jsonl` has SHA-256 `926af2a0…2606`, which equals the sha256 in
  `bird_mini_dev_manifest.json` (revision string `colleague_review_fixes_20261025`).
- What we cannot verify: the card does not publish predictions, the decoding temperature or the
  extraction code, so we cannot check that the 59.60 was computed exactly this way.
