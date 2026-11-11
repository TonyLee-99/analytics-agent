"""Every v2 headline number in the README equals results/v2/headline.json (built from results/)."""

import json

import pytest

from analytics_agent.copilot.settings import ROOT, V2_RESULTS


@pytest.mark.skipif(not (V2_RESULTS / "headline.json").exists(), reason="no headline yet")
def test_readme_v2_numbers():
    readme = (ROOT / "README.md").read_text()
    head = json.loads((V2_RESULTS / "headline.json").read_text())
    missing = {k: v for k, v in head.items() if v != "n/a" and v not in readme}
    assert not missing, missing
