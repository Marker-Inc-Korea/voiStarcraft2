#!/usr/bin/env python3
"""Parse GitHub issue JSON into a deterministic acceptance-tracking report.

The input shape is the JSON emitted by ``gh issue list --json number,title,body``.
No network access is required by the parser itself, which keeps replay and CI
tests independent from GitHub credentials.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s+(.*\S)\s*$")
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
ISSUE_REF_RE = re.compile(r"(?<![\w-])#(\d+)\b")


def _sections(body: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in body.splitlines():
        match = HEADING_RE.match(line)
        if match:
            current = {"level": len(match.group(1)), "title": match.group(2), "lines": []}
            sections.append(current)
        elif current is not None:
            current["lines"].append(line)
    return sections


def parse_issue(issue: dict[str, Any]) -> dict[str, Any]:
    number = int(issue["number"])
    body = str(issue.get("body") or "")
    criteria: list[dict[str, Any]] = []
    for section in _sections(body):
        for line in section["lines"]:
            match = CHECKBOX_RE.match(line)
            if match:
                criteria.append(
                    {
                        "section": section["title"],
                        "text": match.group(2),
                        "complete": match.group(1).lower() == "x",
                    }
                )
    refs = sorted({int(value) for value in ISSUE_REF_RE.findall(body) if int(value) != number})
    return {
        "number": number,
        "title": str(issue.get("title") or ""),
        "url": issue.get("url"),
        "state": issue.get("state"),
        "updated_at": issue.get("updatedAt") or issue.get("updated_at"),
        "labels": [label.get("name", label) if isinstance(label, dict) else label for label in issue.get("labels", [])],
        "references": refs,
        "sections": [
            {"level": section["level"], "title": section["title"], "line_count": len(section["lines"])}
            for section in _sections(body)
        ],
        "acceptance_criteria": criteria,
        "acceptance_counts": {
            "total": len(criteria),
            "complete": sum(item["complete"] for item in criteria),
            "open": sum(not item["complete"] for item in criteria),
        },
    }


def parse_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, list):
        raise ValueError("input must be a JSON array of GitHub issues")
    issues = sorted((parse_issue(item) for item in document if isinstance(item, dict)), key=lambda item: item["number"])
    known = {item["number"] for item in issues}
    edges = [
        {"from": item["number"], "to": ref}
        for item in issues
        for ref in item["references"]
        if ref in known
    ]
    return {
        "schema_version": "issue-parser/v1",
        "issue_count": len(issues),
        "issues": issues,
        "reference_edges": edges,
        "open_acceptance_criteria": sum(item["acceptance_counts"]["open"] for item in issues),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", help="JSON file; defaults to stdin")
    parser.add_argument("-o", "--output", help="write report JSON to this path")
    args = parser.parse_args(argv)
    raw = Path(args.input).read_text(encoding="utf-8") if args.input else sys.stdin.read()
    report = parse_document(json.loads(raw))
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
