import json

from scripts.issue_parser import parse_document, parse_issue


def test_parse_issue_extracts_checklist_sections_and_references():
    parsed = parse_issue(
        {
            "number": 10,
            "title": "Example",
            "body": "## Goal\n- [x] first\n- [ ] second\n\nDepends on #9.",
            "labels": [{"name": "track"}],
        }
    )
    assert parsed["references"] == [9]
    assert parsed["acceptance_counts"] == {"total": 2, "complete": 1, "open": 1}
    assert parsed["acceptance_criteria"][1]["section"] == "Goal"


def test_parse_document_builds_only_internal_reference_edges():
    report = parse_document(
        [
            {"number": 1, "title": "one", "body": "Related #2 and #99"},
            {"number": 2, "title": "two", "body": "child"},
        ]
    )
    assert report["issue_count"] == 2
    assert report["reference_edges"] == [{"from": 1, "to": 2}]


def test_report_is_json_serializable():
    assert json.loads(json.dumps(parse_document([])))["schema_version"] == "issue-parser/v1"
