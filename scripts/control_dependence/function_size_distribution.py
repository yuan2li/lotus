#!/usr/bin/env python3
"""Measure how many analysed functions fit the sparse-row fast path.

The implementation stores inevitability rows and biclique sides as
``llvm::SparseBitVector``, whose ``test()`` walks a list of 128-bit elements.
A vertex identifier below 128 lives in the first element, so membership is
constant-time exactly when a function has at most 127 CFG vertices (the driver
numbers one vertex per basic block, starting at 1).

This script re-runs the Full variants over the real-world subjects of an
existing evaluation, with the same driver flags, and reports:

- the share of functions (and CFG vertices) within the 127-vertex bound;
- the share of Full-Enumerate and Full-Closure analysis time spent in larger
  functions, i.e. the only time the sparse walk can affect.

Outputs ``function_sizes.csv`` (one row per function and algorithm) in the
results directory and ``function_size_macros.tex`` for the paper.
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
SPARSE_ELEMENT_BITS = 128

# (label, driver algorithm, extra flags) mirroring evaluate_control_dependence.py.
VARIANTS = [
    ("Full-Enumerate", "dod-compact", ["--visit-pairs"]),
    ("Full-Closure", "compact-closure", []),
]


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


def evaluated_inputs(results_dir: Path) -> list[Path]:
    seen: dict[str, None] = {}
    with open(results_dir / "summary.csv", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["experiment"] == "rq1-enumeration" and "/synthetic/" not in row["input"]:
                seen.setdefault(row["input"], None)
    return [Path(p) for p in seen]


def expected_totals(results_dir: Path) -> tuple[int, int]:
    functions = nodes = 0
    with open(results_dir / "summary.csv", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["experiment"] == "rq1-enumeration" and "/synthetic/" not in row["input"]:
                functions += int(row["functions"])
                nodes += int(row["nodes"])
    return functions, nodes


def run_variant(tool: Path, bitcode: Path, algorithm: str, flags: list[str],
                timeout: float) -> list[dict[str, str]]:
    command = [str(tool), str(bitcode), f"--algorithm={algorithm}",
               "--format=csv", "--lower-switch=true", *flags]
    completed = subprocess.run(command, text=True, capture_output=True,
                               timeout=timeout, check=True)
    return list(csv.DictReader(io.StringIO(completed.stdout)))


def suite_of(path: Path) -> str:
    parts = path.parts
    for suite in ("SPEC2006", "coreutils", "open"):
        if suite in parts:
            return suite
    return path.parent.name


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
    parser.add_argument("--macro-dir", type=Path,
                        default=WORKSPACE_ROOT / "paper-control-dep" / "sections" / "generated")
    args = parser.parse_args()

    remaps = [tuple(item.split("=", 1)) for item in args.remap]
    # The large real-world bitcode was moved out of the lotus tree; look for it
    # next to lotus by default.
    for suite in ("coreutils", "open"):
        remaps.append((str(LOTUS_ROOT / "benchmarks" / "real-world" / suite),
                       str(WORKSPACE_ROOT / suite)))

    inputs = evaluated_inputs(args.results_dir)
    resolved = {}
    for recorded in inputs:
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

    limit = SPARSE_ELEMENT_BITS - 1
    enum_rows = [r for r in records if r["variant"] == "Full-Enumerate"]
    functions = len(enum_rows)
    nodes = sum(r["nodes"] for r in enum_rows)
    exp_functions, exp_nodes = expected_totals(args.results_dir)
    if (functions, nodes) != (exp_functions, exp_nodes):
        print(f"totals differ from evaluation: {functions} functions / {nodes} vertices "
              f"vs {exp_functions} / {exp_nodes}", file=sys.stderr)
        return 1

    small = [r for r in enum_rows if r["nodes"] <= limit]
    small_pct = 100.0 * len(small) / functions
    large_vertex_pct = 100.0 * sum(r["nodes"] for r in enum_rows if r["nodes"] > limit) / nodes
    max_nodes = max(r["nodes"] for r in enum_rows)

    time_share = {}
    for label, _, _ in VARIANTS:
        rows = [r for r in records if r["variant"] == label]
        total = sum(r["analysis_ns"] for r in rows)
        large = sum(r["analysis_ns"] for r in rows if r["nodes"] > limit)
        time_share[label] = 100.0 * large / total

    print(f"functions={functions:,} vertices={nodes:,} (match evaluation)")
    print(f"<= {limit} vertices: {len(small):,} functions ({small_pct:.2f}%)")
    print(f"vertices in larger functions: {large_vertex_pct:.2f}%  max={max_nodes:,}")
    for label, share in time_share.items():
        print(f"{label}: {share:.2f}% of analysis time in functions > {limit} vertices")
    for suite in ("SPEC2006", "coreutils", "open"):
        rows = [r for r in enum_rows if r["suite"] == suite]
        if rows:
            pct = 100.0 * sum(r["nodes"] <= limit for r in rows) / len(rows)
            print(f"  {suite}: {len(rows):,} functions, {pct:.2f}% within bound")

    macros = [
        "% Auto-generated by function_size_distribution.py",
        f"\\newcommand{{\\SparseFastPathLimit}}{{{limit}}}",
        f"\\newcommand{{\\SparseFastPathFunctionPercent}}{{{small_pct:.1f}}}",
        f"\\newcommand{{\\SparseLargeVertexPercent}}{{{large_vertex_pct:.1f}}}",
        f"\\newcommand{{\\SparseMaxFunctionVertices}}{{{max_nodes:,}}}",
        f"\\newcommand{{\\SparseLargeEnumTimePercent}}{{{time_share['Full-Enumerate']:.1f}}}",
        f"\\newcommand{{\\SparseLargeClosureTimePercent}}{{{time_share['Full-Closure']:.1f}}}",
    ]
    args.macro_dir.mkdir(parents=True, exist_ok=True)
    (args.macro_dir / "function_size_macros.tex").write_text("\n".join(macros) + "\n")
    print(f"wrote {out_csv} and {args.macro_dir / 'function_size_macros.tex'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
