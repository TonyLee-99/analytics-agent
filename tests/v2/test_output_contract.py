"""Answers never expose prompt internals (section names, placeholders, meta notes)."""

import pytest

from analytics_agent.copilot.copilot import NOT_FOUND, scrub_internal

from .helpers import mock_llm, small_copilot


@pytest.mark.parametrize("raw,clean", [
    ("CDOT got **4,211** [S1]. No governed metric definition was used, since no DATA was provided.", "CDOT got **4,211** [S1]."),
    ("[No SOURCES were provided, so this answer is based only on DATA.]\nThe median was 3.5 days.", "The median was 3.5 days."),
    ("The DATA shows 3.5 days. Median was 3.5 days [S2].", "Median was 3.5 days [S2]."),
    ("Requests rose 4% [DATA].", "Requests rose 4%."),
    ("The data covers 2025 and the sources agree [S1].", "The data covers 2025 and the sources agree [S1]."),
])
def test_scrub(raw, clean):
    assert scrub_internal(raw)[0] == clean


def test_all_internal_becomes_not_found():
    assert scrub_internal("The SOURCES section is empty.")[0] == NOT_FOUND


def test_copilot_applies_contract_and_traces_removal(tmp_path):
    cp = small_copilot(tmp_path, llm=mock_llm(answer="Patched by CDOT crews [S1]. NOTICES were empty."))
    a = cp.ask("maria", "how are potholes fixed?")
    assert a.text == "Patched by CDOT crews [S1]." and a.trace["internal_text_removed"] == ["NOTICES were empty."]
