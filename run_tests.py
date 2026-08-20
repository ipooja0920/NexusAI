#!/usr/bin/env python3
"""Run the NexusAI test suite.

    python run_tests.py           # run everything
    python run_tests.py -v        # show each test name

Safe to run any time: every test builds its own throwaway project in a
temp folder and uses a stubbed AI client. Nothing here reads or writes
your data/ folder, and no API key or network connection is needed.
"""
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

SUITES = [
    ("Scoring math", "tests.test_scoring"),
    ("Excel read/write", "tests.test_data_io"),
    ("Pre-filter & geocoding", "tests.test_prefilter"),
    ("End-to-end pipeline", "tests.test_pipeline"),
]


def main() -> int:
    verbose = "-v" in sys.argv or "--verbose" in sys.argv

    print("\n" + "=" * 62)
    print("  NexusAI test suite")
    print("  (isolated temp projects, stubbed AI, no API key needed)")
    print("=" * 62)

    loader = unittest.TestLoader()
    total_run = total_failed = 0
    results = []

    for label, module in SUITES:
        suite = loader.loadTestsFromName(module)
        stream = sys.stdout if verbose else open("/dev/null", "w")
        runner = unittest.TextTestRunner(
            stream=stream, verbosity=2 if verbose else 0, buffer=not verbose)
        result = runner.run(suite)
        if not verbose:
            stream.close()

        failed = len(result.failures) + len(result.errors)
        total_run += result.testsRun
        total_failed += failed
        results.append((label, result.testsRun, failed, result))

        mark = "PASS" if failed == 0 else "FAIL"
        print(f"  [{mark}]  {label:<26} {result.testsRun - failed}/{result.testsRun}")

    print("=" * 62)
    if total_failed == 0:
        print(f"  All {total_run} tests passed.")
    else:
        print(f"  {total_failed} of {total_run} tests FAILED.\n")
        for label, _, failed, result in results:
            for case, trace in result.failures + result.errors:
                print(f"  --- {label}: {case} ---")
                print("  " + trace.strip().replace("\n", "\n  ")[:1200])
                print()
    print("=" * 62 + "\n")
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
