#!/usr/bin/env python3
"""Hidden checker for data-cleanup/ledger-reconcile.

Usage: python3 check.py <deliverables_dir>

Reads <deliverables_dir>/output/reconciliation.md, recomputes the true totals and the
unmatched-row count from workspace/bank.csv and workspace/ledger.csv next to this file, and
prints {"n_passed": int, "n_criteria": 3}. Stdlib only.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
N_CRITERIA = 3


def _amount(raw: str) -> Decimal:
    return Decimal(raw.strip().replace(",", "").replace("$", "")).quantize(Decimal("0.01"))


def _ledger_date(raw: str) -> str:
    day, month, year = raw.strip().split("/")
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def truth() -> tuple[Decimal, Decimal, int]:
    with open(HERE / "workspace" / "bank.csv", newline="", encoding="utf-8") as fh:
        bank = [(r["date"].strip(), _amount(r["amount"])) for r in csv.DictReader(fh)]
    with open(HERE / "workspace" / "ledger.csv", newline="", encoding="utf-8") as fh:
        ledger = [(_ledger_date(r["Posted"]), _amount(r["Amount"])) for r in csv.DictReader(fh)]
    b, lg = Counter(bank), Counter(ledger)
    matched = sum((b & lg).values())
    unmatched = (len(bank) - matched) + (len(ledger) - matched)
    return sum(a for _, a in bank), sum(a for _, a in ledger), unmatched


def _labelled_amounts(text: str, label: str) -> list[Decimal]:
    """Every two-decimal amount that follows `label` on the same line."""
    pat = re.compile(
        rf"{label}[^0-9\-\n]*(-)?\s*\$?\s*(-)?(\d[\d,]*\.\d{{2}})(?!\d)", re.IGNORECASE
    )
    out = []
    for m in pat.finditer(text):
        sign = "-" if (m.group(1) or m.group(2)) else ""
        out.append(Decimal(sign + m.group(3).replace(",", "")))
    return out


def grade(text: str) -> int:
    bank_total, ledger_total, unmatched = truth()
    passed = 0
    if bank_total in _labelled_amounts(text, r"bank\s+total"):
        passed += 1
    if ledger_total in _labelled_amounts(text, r"ledger\s+total"):
        passed += 1
    counts = re.findall(r"unmatched\s+rows[^0-9\n]*?(\d+)", text, re.IGNORECASE)
    if counts and int(counts[0]) == unmatched:
        passed += 1
    return passed


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python3 check.py <deliverables_dir>", file=sys.stderr)
        return 2
    path = Path(argv[1]) / "output" / "reconciliation.md"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    print(json.dumps({"n_passed": grade(text) if text else 0, "n_criteria": N_CRITERIA}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
