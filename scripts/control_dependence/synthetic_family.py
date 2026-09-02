#!/usr/bin/env python3
"""Synthetic graph family generator and benchmark based on Proposition 5.1.

Proposition 5.1:
For every k >= 1, there is a digraph of maximum out-degree two on 3k vertices
with k^3 triples, while the biclique representation contains 2k^2 incidences.

Graph construction:
- Cycle vertices: x_1 -> x_2 -> ... -> x_k -> y_1 -> y_2 -> ... -> y_k -> x_1
- Decision vertices: p_1, ..., p_k, each with successors x_1 and y_1.
- Total vertices n = 3k.
- Total DOD triples K = k * (k * k) = k^3 = (n/3)^3.
- Total Biclique incidences C = k * (k + k) = 2k^2 = 2 * (n/3)^2.
- Ratio K/C = k / 2 = n / 6.

This script provides:
1. Pure Python synthetic graph benchmark measuring scaling of Compact DOD,
   Explicit Enumeration, Compact Closure, and Explicit Closure.
2. LLVM IR (.ll) generator to produce benchmark bitcode files for the C++ driver.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

# Ensure we can import DiGraph from paper-control-dep/compact_dod_reference.py
LOTUS_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = LOTUS_ROOT.parent
sys.path.insert(0, str(WORKSPACE_ROOT / "paper-control-dep"))

from compact_dod_reference import DiGraph, CompactDOD


def build_synthetic_graph(k: int) -> DiGraph:
    """Construct the Proposition 5.1 graph on 3k vertices.

    Vertex indexing:
    - x_0, ..., x_{k-1}: indices 0 ... k-1
    - y_0, ..., y_{k-1}: indices k ... 2k-1
    - p_0, ..., p_{k-1}: indices 2k ... 3k-1
    """
    n = 3 * k
    adjacency: List[List[int]] = [[] for _ in range(n)]

    # x-chain: x_0 -> x_1 -> ... -> x_{k-1} -> y_0
    for i in range(k - 1):
        adjacency[i].append(i + 1)
    adjacency[k - 1].append(k)  # x_{k-1} -> y_0

    # y-chain: y_0 -> y_1 -> ... -> y_{k-1} -> x_0
    for j in range(k - 1):
        adjacency[k + j].append(k + j + 1)
    adjacency[2 * k - 1].append(0)  # y_{k-1} -> x_0

    # decisions: p_i -> x_0 and p_i -> y_0
    for d in range(k):
        p_idx = 2 * k + d
        adjacency[p_idx].append(0)  # x_0
        adjacency[p_idx].append(k)  # y_0

    return DiGraph(adjacency)


def generate_synthetic_llvm_ir(k: int) -> str:
    """Generate an LLVM IR module (.ll) matching Proposition 5.1 structure."""
    lines = [
        "; ModuleID = 'synthetic_k{}.ll'".format(k),
        'source_filename = "synthetic_k{}.c"'.format(k),
        "target datalayout = \"e-m:e-p270:32:32-p271:32:32-p272:64:64-i64:64-f80:128-n8:16:32:64-S128\"",
        'target triple = "x86_64-unknown-linux-gnu"',
        "",
        "define void @synthetic_func(i32 %cond) {",
        "entry:",
        "  br label %p_0",
        "",
    ]

    # Decisions p_0 ... p_{k-1}
    for d in range(k):
        next_label = f"p_{d+1}" if d + 1 < k else "x_0"
        lines.extend([
            f"p_{d}:",
            f"  %cmp_{d} = icmp eq i32 %cond, {d}",
            f"  br i1 %cmp_{d}, label %x_0, label %y_0",
            "",
        ])

    # x_0 ... x_{k-1}
    for i in range(k):
        next_label = f"x_{i+1}" if i + 1 < k else "y_0"
        lines.extend([
            f"x_{i}:",
            f"  br label %{next_label}",
            "",
        ])

    # y_0 ... y_{k-1}
    for j in range(k):
        next_label = f"y_{j+1}" if j + 1 < k else "x_0"
        lines.extend([
            f"y_{j}:",
            f"  br label %{next_label}",
            "",
        ])

    lines.extend([
        "  ret void",
        "}",
        "",
    ])
    return "\n".join(lines)


def benchmark_synthetic(k_values: List[int]) -> List[Dict[str, object]]:
    """Measure the scaling behavior across increasing k."""
    results = []
    for k in k_values:
        n = 3 * k
        g = build_synthetic_graph(k)

        # 1. Inevitability
        t0 = time.perf_counter_ns()
        rows = g.inevitable_rows()
        t_inev_ns = time.perf_counter_ns() - t0

        # 2. Compact DOD build
        t0 = time.perf_counter_ns()
        compact = g.compact_dod(rows)
        t_compact_build_ns = time.perf_counter_ns() - t0

        # 3. Explicit enumeration from compact
        t0 = time.perf_counter_ns()
        explicit = g.explicit_dod(compact)
        t_explicit_enum_ns = time.perf_counter_ns() - t0

        # 4. NTSCD
        t0 = time.perf_counter_ns()
        ntscd = g.ntscd(rows)
        t_ntscd_ns = time.perf_counter_ns() - t0

        # Structural metrics
        num_triples = len(explicit)
        num_bicliques = len(compact)
        num_incidences = sum(len(c.left) + len(c.right) for c in compact.values())

        # 5. Compact Closure (seed includes root p_0 and one cycle node)
        seed = [2 * k, 1, k + 1]  # p_0, x_1, y_1
        t0 = time.perf_counter_ns()
        compact_closure_res = g.rooted_dependency_closure(seed, ntscd, compact)
        t_compact_closure_ns = time.perf_counter_ns() - t0

        # 6. Explicit Pair Closure
        def explicit_closure_run(seed_nodes, ntscd_set, dod_set):
            res = set(seed_nodes)
            while True:
                old_sz = len(res)
                for p, target in ntscd_set:
                    if target in res:
                        res.add(p)
                for p, a, b in dod_set:
                    if a in res and b in res:
                        res.add(p)
                if len(res) == old_sz:
                    break
            return res

        t0 = time.perf_counter_ns()
        explicit_closure_res = explicit_closure_run(seed, ntscd, explicit)
        t_explicit_closure_ns = time.perf_counter_ns() - t0

        assert compact_closure_res == explicit_closure_res, "Closure mismatch!"
        assert num_triples == k**3, f"Expected {k**3} triples, got {num_triples}"
        assert num_incidences == 2 * k**2, f"Expected {2*k**2} incidences, got {num_incidences}"

        row = {
            "k": k,
            "n": n,
            "decisions": k,
            "expected_triples_K": k**3,
            "actual_triples_K": num_triples,
            "expected_incidences_C": 2 * k**2,
            "actual_incidences_C": num_incidences,
            "compression_ratio_K_over_C": num_triples / num_incidences if num_incidences else 1.0,
            "inevitability_ms": t_inev_ns / 1e6,
            "compact_build_ms": t_compact_build_ns / 1e6,
            "explicit_enum_ms": t_explicit_enum_ns / 1e6,
            "compact_closure_ms": t_compact_closure_ns / 1e6,
            "explicit_closure_ms": t_explicit_closure_ns / 1e6,
            "closure_speedup": (
                t_explicit_closure_ns / t_compact_closure_ns
                if t_compact_closure_ns > 0
                else 1.0
            ),
        }
        results.append(row)
        print(
            f"k={k:3d} (n={n:4d}) | Triples K={num_triples:8d}, Incidences C={num_incidences:6d} | "
            f"Ratio K/C={row['compression_ratio_K_over_C']:6.1f}x | "
            f"CompactBuild: {row['compact_build_ms']:8.3f}ms | "
            f"ExplicitEnum: {row['explicit_enum_ms']:8.3f}ms | "
            f"ClosureSpeedup: {row['closure_speedup']:6.2f}x"
        )
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--generate-ir",
        type=int,
        metavar="K",
        help="Generate synthetic LLVM IR (.ll) for given k and write to stdout",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=LOTUS_ROOT / "benchmarks" / "synthetic",
        help="Directory to store generated synthetic .ll benchmarks",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run Python synthetic scaling benchmark",
    )
    parser.add_argument(
        "--k-values",
        type=int,
        nargs="+",
        default=[5, 10, 20, 30, 40, 50, 75, 100],
        help="List of k values for benchmark",
    )
    parser.add_argument(
        "--generate-suite",
        action="store_true",
        help="Generate synthetic benchmark suite (.ll files) in output-dir",
    )
    args = parser.parse_args()

    if args.generate_ir:
        print(generate_synthetic_llvm_ir(args.generate_ir))
        return

    if args.generate_suite:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for k in args.k_values:
            target_file = args.output_dir / f"synthetic_k{k}.ll"
            target_file.write_text(generate_synthetic_llvm_ir(k))
            print(f"Generated {target_file} (k={k}, n={3*k}, K={k**3})")

    if args.benchmark or not (args.generate_ir or args.generate_suite):
        print("=== Running Synthetic Benchmark (Proposition 5.1 Verification) ===")
        results = benchmark_synthetic(args.k_values)
        out_csv = WORKSPACE_ROOT / "synthetic_benchmark_results.csv"
        with out_csv.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            writer.writeheader()
            writer.writerows(results)
        print(f"\nResults written to {out_csv}")


if __name__ == "__main__":
    main()
