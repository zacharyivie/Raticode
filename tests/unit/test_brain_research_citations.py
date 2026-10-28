from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from gofer.ui.brain_library import BrainLibrary
from gofer.ui.brain_research import checked_research_citations, pinned_quote
from tests.unit.test_brain_library import opened


@pytest.mark.parametrize(
    "text,quote,exact",
    [
        ("An exact\n  passage.", "An exact passage.", "An exact\n  passage."),
        ("The ‘quoted’\u00a0text.", "The 'quoted' text.", "The ‘quoted’\u00a0text."),
        ("A cafe\u0301 opened.", "A café opened.", "A cafe\u0301 opened."),
        ("Evidence costs 100 dollars.", "Evidence costs 10 dollars.", None),
        ("Alpha separated from omega.", "Alpha...omega.", None),
        ("Exact words.", "exact words.", None),
        ("A passage.", "  ", None),
    ],
)
def test_quote_formatting_restores_exact_source_text_without_changing_words(text, quote, exact):
    assert pinned_quote(text, quote) == exact


def test_quote_cannot_be_reattributed_to_another_passage():
    evidence = [
        {"id": "e1", "text": "First observation."},
        {"id": "e2", "text": "Different evidence."},
    ]
    output = {
        "claims": [
            {"text": "Claim", "citations": [{"evidence": "e1", "quote": "Different evidence."}]}
        ]
    }
    _, problems = checked_research_citations(output, evidence)
    assert len(problems) == 1 and problems[0]["evidence"] == "e1"


@pytest.fixture
def library(tmp_path):
    service = BrainLibrary(tmp_path / "app", start=False)
    yield service
    service.close()


def response(evidence_id: str, quote: str) -> dict[str, Any]:
    return {
        "title": "Findings",
        "claims": [
            {
                "text": "A supported finding.",
                "support": "paraphrased",
                "citations": [{"evidence": evidence_id, "quote": quote}],
            }
        ],
    }


@pytest.mark.parametrize("failure", ["quote", "id"])
def test_report_corrects_invalid_citation_once_using_same_pinned_sources(
    library, tmp_path, failure
):
    brain, doc = opened(library, tmp_path)
    calls = []

    def generate(prompt, provider, cancel):
        calls.append(prompt)
        assert provider == {"id": "cursor"}
        if len(calls) == 1:
            return response("wrong-id" if failure == "id" else "e2", "Invented quote")
        assert "Citation correction:" in prompt
        assert (
            "Unknown evidence ID" in prompt if failure == "id" else "Quote is not present" in prompt
        )
        evidence = json.loads(prompt.split("Evidence:\n")[1].split("\nCitation correction:")[0])
        source = next(item for item in evidence if "exact evidence" in item["text"])
        return response(source["id"], "exact evidence")

    library.generate = generate
    result = library.report(
        {
            "brainId": brain["id"],
            "docIds": [doc["id"]],
            "question": "Evidence?",
            "provider": {"id": "cursor"},
            "allowCloudForTask": True,
        },
        threading.Event(),
        lambda _: None,
    )
    report = library.entity("reports", result["reportId"])
    assert len(calls) == 2
    assert report["citations"][0]["quote"] == "exact evidence"
    assert report["citations"][0]["quote"] in report["citations"][0]["snapshot"]


@pytest.mark.parametrize(
    "budget,cancelled,expected_calls", [(100, False, 2), (1, False, 1), (100, True, 1)]
)
def test_failed_correction_never_publishes_and_respects_budget_and_cancellation(
    library, tmp_path, budget, cancelled, expected_calls
):
    brain, doc = opened(library, tmp_path)
    calls = []
    cancel = threading.Event()

    def generate(prompt, provider, event):
        calls.append(prompt)
        if cancelled:
            event.set()
        return response("e1", "Invented")

    library.generate = generate
    with pytest.raises(InterruptedError if cancelled else ValueError):
        library.report(
            {
                "brainId": brain["id"],
                "docIds": [doc["id"]],
                "question": "Evidence?",
                "allowCloudForTask": True,
                "turnBudget": budget,
            },
            cancel,
            lambda _: None,
        )
    assert len(calls) == expected_calls
    assert library.entities("reports") == []
    assert not (tmp_path / "brain" / "Reports").exists()


def test_final_summary_cannot_cite_a_synthetic_join_of_separate_quotes(library, tmp_path):
    brain, doc = opened(library, tmp_path)
    doc["blocks"] = [
        {"id": f"b{i}", "text": "Beginning. " + "filler " * 12000 + "Ending."} for i in range(3)
    ]
    library.put("documents", doc)
    calls = []

    def generate(prompt, provider, cancel):
        calls.append(prompt)
        if len(calls) == 1:
            partial = response("e1", "Beginning.")
            partial["claims"][0]["citations"].append({"evidence": "e1", "quote": "Ending."})
            return partial
        if len(calls) == 2:
            return response("e1", "Beginning.\n\nEnding.")
        assert "Citation correction:" in prompt
        return response("e1", "Beginning.")

    library.generate = generate
    result = library.report(
        {
            "brainId": brain["id"],
            "docIds": [doc["id"]],
            "question": "Evidence?",
            "allowCloudForTask": True,
        },
        threading.Event(),
        lambda _: None,
    )
    assert len(calls) == 3
    assert library.entity("reports", result["reportId"])["citations"][0]["quote"] == "Beginning."


@pytest.mark.parametrize("budget", [3, 4])
def test_intermediate_correction_reserves_turns_for_remaining_batches_and_final_report(
    library, tmp_path, budget
):
    brain, doc = opened(library, tmp_path)
    doc["blocks"] = [
        {"id": f"b{i}", "text": f"Observation {i}. " + "filler " * 460} for i in range(81)
    ]
    library.put("documents", doc)
    calls = []

    def generate(prompt, provider, cancel):
        calls.append(prompt)
        if len(calls) == 1:
            return response("e1", "Invented")
        evidence = json.loads(
            prompt.split("Evidence:\n")[1]
            .split("\nIntermediate findings:")[0]
            .split("\nCitation correction:")[0]
        )
        source = evidence[0]
        return response(source["id"], source["text"].split(".")[0] + ".")

    library.generate = generate
    params = {
        "brainId": brain["id"],
        "docIds": [doc["id"]],
        "question": "Evidence?",
        "allowCloudForTask": True,
        "turnBudget": budget,
    }
    if budget == 3:
        with pytest.raises(ValueError, match="does not match"):
            library.report(params, threading.Event(), lambda _: None)
        assert len(calls) == 1
        assert not library.entities("reports")
    else:
        result = library.report(params, threading.Event(), lambda _: None)
        assert len(calls) == 4
        assert library.entity("reports", result["reportId"])["citations"]


def test_formatting_recovery_publishes_original_quote_and_selector_without_an_extra_turn(
    library, tmp_path
):
    brain, doc = opened(library, tmp_path)
    doc["blocks"] = [{"id": "formatting", "text": "The ‘exact’\n  evidence."}]
    library.put("documents", doc)
    calls = []

    def generate(prompt, provider, cancel):
        calls.append(prompt)
        return response("e1", "The 'exact' evidence.")

    library.generate = generate
    result = library.report(
        {
            "brainId": brain["id"],
            "docIds": [doc["id"]],
            "question": "Evidence?",
            "allowCloudForTask": True,
        },
        threading.Event(),
        lambda _: None,
    )
    citation = library.entity("reports", result["reportId"])["citations"][0]
    assert len(calls) == 1
    assert citation["quote"] == citation["selector"]["exact"] == doc["blocks"][0]["text"]
