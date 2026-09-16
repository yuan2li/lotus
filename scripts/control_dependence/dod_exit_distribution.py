#!/usr/bin/env python3
"""Report where the biclique construction leaves each binary decision.

Runs the driver's ``--dod-exit-stats`` mode over the real-world subjects of an
existing evaluation and aggregates the per-decision exit reasons:

- ``single_entry``   the two branches share a single projection entry
                     (|B1 u B2| <= 1), so no order can differ;
- ``shared_entry``   the branch-entry sets overlap;
- ``decision_entry`` the decision itself is a branch entry;
- ``no_cycle``       the projection is not one directed cycle;
- ``transitions``    the compressed label sequence has other than two
                     transitions;
- ``biclique``       a non-empty biclique was returned.

This explains structurally why the order relation is empty on real CFGs.  The
mode does no timing, so it can run after the timed evaluation without
disturbing it.

Outputs ``dod_exit_stats.csv`` (one row per function) in the results directory
and prints the aggregate distribution.
"""

from __future__ import annotations

import argparse
import csv
import io
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

LOTUS_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = LOTUS_ROOT.parent

REASONS = ["single_entry", "shared_entry", "decision_entry", "no_cycle",
           "transitions", "biclique"]


def resolve(path: Path, remaps: list[tuple[str, str]]) -> Path | None:
    if path.exists():
        return path
    text = str(path)
    for old, new in remaps:
        if text.startswith(old):
            candidate = Path(new + text[len(old):])
            if candidate.exists():
                return candidate
    return None


def real_world_inputs(results_dir: Path) -> list[Path]:
    seen: dict[str, None] = {}
    with open(results_dir / "summary.csv", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["experiment"] == "rq1-enumeration" and "/synthetic/" not in row["input"]:
                seen.setdefault(row["input"], None)
    return [Path(p) for p in seen]


def run_subject(tool: Path, bitcode: Path, timeout: float) -> list[dict[str, str]]:
    command = [str(tool), str(bitcode), "--dod-exit-stats", "--lower-switch=true"]
    completed = subprocess.run(command, text=True, capture_output=True,
                               timeout=timeout, check=True)
    return list(csv.DictReader(io.StringIO(completed.stdout)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results-dir", type=Path,
                        default=LOTUS_ROOT / "control-dependence-results")
    parser.add_argument("--tool", type=Path,
                        default=LOTUS_ROOT / "build-release" / "bin" / "lotus-ir-control-dependence")
    parser.add_argument("--remap", action="append", default=[], metavar="OLD=NEW",
                        help="rewrite an input path prefix when the recorded path is gone")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()

    remaps = [tuple(item.split("=", 1)) for item in args.remap]
    for suite in ("coreutils", "open"):
        remaps.append((str(LOTUS_ROOT / "benchmarks" / "real-world" / suite),
                       str(WORKSPACE_ROOT / suite)))

    resolved = {}
    for recorded in real_world_inputs(args.results_dir):
        actual = resolve(recorded, remaps)
        if actual is None:
            print(f"missing bitcode: {recorded}", file=sys.stderr)
            return 1
        resolved[recorded] = actual
    print(f"{len(resolved)} real-world subjects", file=sys.stderr)

    records: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_subject, args.tool, actual, args.timeout): recorded
                   for recorded, actual in resolved.items()}
        for done, future in enumerate(as_completed(futures), 1):
            recorded = futures[future]
            for row in future.result():
                record = {"input": str(recorded), "function": row["function"],
                          "decisions": int(row["decisions"])}
                record.update({reason: int(row[reason]) for reason in REASONS})
                records.append(record)
            print(f"[{done}/{len(resolved)}] {Path(recorded).name}", file=sys.stderr)

    out_csv = args.results_dir / "dod_exit_stats.csv"
    with open(out_csv, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(sorted(records, key=lambda r: (r["input"], r["function"])))

    decisions = sum(r["decisions"] for r in records)
    counted = {reason: sum(r[reason] for r in records) for reason in REASONS}
    print(f"functions={len(records):,} binary decisions={decisions:,}")
    for reason in REASONS:
        share = 100.0 * counted[reason] / decisions if decisions else 0.0
        print(f"  {reason:15s} {counted[reason]:12,} {share:6.2f}%")
    unaccounted = decisions - sum(counted.values())
    if unaccounted:
        print(f"  {'unaccounted':15s} {unaccounted:12,}", file=sys.stderr)
        return 1
    print(f"wrote {out_csv}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
