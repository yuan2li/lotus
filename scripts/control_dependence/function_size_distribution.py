#!/usr/bin/env python3
"""Describe the size distribution of the evaluated functions.

Re-runs the Full variants over the real-world subjects of an existing
evaluation, with the same driver flags, and reports:

- percentiles of the per-function CFG vertex count (one vertex per basic block
  after switch lowering, as the driver analyses them);
- for each size bucket, its share of functions, of CFG vertices, and of
  Full-Enumerate and Full-Closure analysis time.

This shows where the analysis time goes (typically a few very large
functions), which is useful when interpreting per-subject timings.

Outputs ``function_sizes.csv`` (one row per function and variant) in the
results directory and prints a summary.
"""

from __future__ import annotations

import argparse
import csv
import io
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

LOTUS_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = LOTUS_ROOT.parent

# (label, driver algorithm, extra flags) mirroring evaluate_control_dependence.py.
VARIANTS = [
    ("Full-Enumerate", "dod-compact", ["--visit-pairs"]),
    ("Full-Closure", "compact-closure", []),
]

# Upper bounds (inclusive) of the reported size buckets, in CFG vertices.
BUCKETS = [16, 64, 256, 1024, 4096]


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


def real_world_rows(results_dir: Path) -> list[dict[str, str]]:
    with open(results_dir / "summary.csv", newline="") as handle:
        return [row for row in csv.DictReader(handle)
                if row["experiment"] == "rq1-enumeration"
                and "/synthetic/" not in row["input"]]


def run_variant(tool: Path, bitcode: Path, algorithm: str, flags: list[str],
                timeout: float) -> list[dict[str, str]]:
    command = [str(tool), str(bitcode), f"--algorithm={algorithm}",
               "--format=csv", "--lower-switch=true", *flags]
    completed = subprocess.run(command, text=True, capture_output=True,
                               timeout=timeout, check=True)
    return list(csv.DictReader(io.StringIO(completed.stdout)))


def suite_of(path: Path) -> str:
    for suite in ("SPEC2006", "coreutils", "open"):
        if suite in path.parts:
            return suite
    return path.parent.name


def bucket_label(index: int) -> str:
    low = 1 if index == 0 else BUCKETS[index - 1] + 1
    return f">{BUCKETS[-1]}" if index == len(BUCKETS) else f"{low}-{BUCKETS[index]}"


def bucket_of(nodes: int) -> int:
    for index, bound in enumerate(BUCKETS):
        if nodes <= bound:
            return index
    return len(BUCKETS)


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
    # The large real-world bitcode was moved out of the lotus tree; look for it
    # next to lotus by default.
    for suite in ("coreutils", "open"):
        remaps.append((str(LOTUS_ROOT / "benchmarks" / "real-world" / suite),
                       str(WORKSPACE_ROOT / suite)))

    summary = real_world_rows(args.results_dir)
    resolved = {}
    for row in summary:
        recorded = Path(row["input"])
        actual = resolve(recorded, remaps)
        if actual is None:
            print(f"missing bitcode: {recorded}", file=sys.stderr)
            return 1
        resolved[recorded] = actual
    print(f"{len(resolved)} real-world subjects", file=sys.stderr)

    records: list[dict[str, object]] = []
    jobs = [(rec, act, label, alg, flags)
            for rec, act in resolved.items() for label, alg, flags in VARIANTS]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_variant, args.tool, act, alg, flags, args.timeout):
                   (rec, act, label) for rec, act, label, alg, flags in jobs}
        for done, future in enumerate(as_completed(futures), 1):
            recorded, actual, label = futures[future]
            for row in future.result():
                records.append({
                    "input": str(recorded), "suite": suite_of(actual),
                    "variant": label, "function": row["function"],
                    "nodes": int(row["nodes"]), "analysis_ns": int(row["analysis_ns"]),
                })
            print(f"[{done}/{len(jobs)}] {label} {actual.name}", file=sys.stderr)

    out_csv = args.results_dir / "function_sizes.csv"
    with open(out_csv, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(sorted(records, key=lambda r: (r["input"], r["variant"], r["function"])))

    enum_rows = [r for r in records if r["variant"] == "Full-Enumerate"]
    functions = len(enum_rows)
    nodes = sum(r["nodes"] for r in enum_rows)
    expected_functions = sum(int(row["functions"]) for row in summary)
    expected_nodes = sum(int(row["nodes"]) for row in summary)
    if (functions, nodes) != (expected_functions, expected_nodes):
        print(f"totals differ from evaluation: {functions} functions / {nodes} vertices "
              f"vs {expected_functions} / {expected_nodes}", file=sys.stderr)
        return 1

    sizes = sorted(r["nodes"] for r in enum_rows)
    quantiles = statistics.quantiles(sizes, n=100)
    print(f"functions={functions:,} vertices={nodes:,} (match evaluation)")
    print(f"vertices per function: median={statistics.median(sizes):g} "
          f"p90={quantiles[89]:g} p99={quantiles[98]:g} max={sizes[-1]:,}")

    totals = {label: sum(r["analysis_ns"] for r in records if r["variant"] == label)
              for label, _, _ in VARIANTS}
    header = f"{'vertices':>11s} {'functions':>10s} {'vertices %':>11s}" + "".join(
        f" {label + ' time %':>22s}" for label, _, _ in VARIANTS)
    print(header)
    for index in range(len(BUCKETS) + 1):
        in_bucket = [r for r in enum_rows if bucket_of(r["nodes"]) == index]
        line = (f"{bucket_label(index):>11s} {100.0 * len(in_bucket) / functions:9.2f}% "
                f"{100.0 * sum(r['nodes'] for r in in_bucket) / nodes:10.2f}%")
        for label, _, _ in VARIANTS:
            share = sum(r["analysis_ns"] for r in records
                        if r["variant"] == label and bucket_of(r["nodes"]) == index)
            line += f" {100.0 * share / totals[label]:21.2f}%"
        print(line)
    print(f"wrote {out_csv}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
