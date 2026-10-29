"""CLI for the eval suite (E02): runs the scenarios in ambit_codebook.evals against the
real tools on AWS, prints a table, writes report.json, and gates on required failures.

    PYTHONPATH=src python evals/eval_suite.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ambit_codebook.evals import run_suite  # noqa: E402


def main() -> None:
    report = run_suite()
    print(f"\n{'ID':<8}{'PASS':<6}{'REQ':<5}SCENARIO")
    print("-" * 78)
    for r in report["results"]:
        print(f"{r['id']:<8}{'PASS' if r['passed'] else 'FAIL':<6}"
              f"{'yes' if r['required'] else '-':<5}{r['name']}")
        print(f"        -> {r['detail']}")
    out = Path(__file__).resolve().parent / "report.json"
    out.write_text(json.dumps(report, indent=2))
    print("-" * 78)
    print(f"{report['passed']}/{report['total']} passed; "
          f"required failures: {report['required_failures']}; report -> {out}")
    sys.exit(1 if report["required_failures"] else 0)


if __name__ == "__main__":
    main()
