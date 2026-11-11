# Corpus: public City of Chicago documents

Fetched by `scripts/v2/fetch_corpus.py` (descriptive User-Agent, robots.txt honoured, >= 1.5 s between
requests per host, backoff on 429/5xx, response cache). `manifest.jsonl` lists every document with its
source URL, fetch time, sha256 of the extracted text and the terms it is used under.

- `portal-*`: dataset metadata (name, description, column descriptions) from the City of Chicago Data
  Portal views API, used under the portal's Terms of Use.
- `web-*`: text of public chicago.gov department pages, reproduced for research with attribution.
- Budget PDFs linked from the Office of Budget and Management page answered HTTP 403 to the automated
  client and were not fetched (no attempt was made to work around it).

Which document is restricted to whom in the demo is **Simulated** configuration: `config/doc_acl.yaml`.
