"""Every headline number in the README must equal the value computed from results/*.json
(scripts/build_headline.py -> results/headline.json). Re-running an analysis that changes a number
without updating the README fails here."""

import json

from analytics_agent.config import RESULTS, ROOT


def test_every_headline_number_appears_in_readme():
    readme = (ROOT / "README.md").read_text()
    head = json.loads((RESULTS / "headline.json").read_text())
    missing = {k: v for k, v in head.items() if v not in readme}
    assert not missing, f"README is out of date for: {missing}"


def test_readme_has_no_local_paths():
    text = (ROOT / "README.md").read_text()
    for bad in ["/Users/", "~/projects"]:
        assert bad not in text, bad
