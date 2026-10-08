#!/usr/bin/env python3
"""Reproducible validation harness for compact_dod_reference.py.

The checks are definition-level regressions, not proofs.  In full mode the
harness exhausts all labelled digraphs on four vertices, compares compact DOD
with a direct checker on randomized larger graphs, and compares compact closure
with explicit pair-by-pair fixed-point closure.
"""

from __future__ import annotations

import argparse
import random
import time
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from compact_dod_reference import DiGraph


Triple = Tuple[int, int, int]
Pair = Tuple[int, int]


def graph_from_mask(n: int, mask: int) -> DiGraph:
    adjacency: List[List[int]] = [[] for _ in range(n)]
    for u in range(n):
        base = u * n
        adjacency[u] = [v for v in range(n) if (mask >> (base + v)) & 1]
    return DiGraph(adjacency)


def random_graph(rng: random.Random, n: int, max_outdegree: Optional[int]) -> DiGraph:
    adjacency: List[List[int]] = []
    for _ in range(n):
        if max_outdegree is None:
            # Vary density across instances rather than fixing one Bernoulli rate.
            edge_probability = rng.random()
            row = [v for v in range(n) if rng.random() < edge_probability]
        else:
            degree = rng.randrange(min(max_outdegree, n) + 1)
            row = rng.sample(range(n), degree)
        adjacency.append(row)
    return DiGraph(adjacency)


def validate_dod(graph: DiGraph) -> None:
    rows = graph.inevitable_rows()
    compact = graph.compact_dod(rows)
    explicit = graph.explicit_dod(compact)
    brute = graph.brute_dod(rows)
    if explicit != brute:
        missing = sorted(brute - explicit)
        extra = sorted(explicit - brute)
        raise AssertionError(
            "DOD mismatch\n"
            f"adjacency={graph.succ}\n"
            f"missing={missing}\n"
            f"extra={extra}\n"
            f"compact={compact}"
        )


def explicit_closure(
    n: int,
    seed: Iterable[int],
    ntscd: Set[Pair],
    dod: Set[Triple],
) -> Set[int]:
    result = set(seed)
    while True:
        old_size = len(result)
        for p, target in ntscd:
            if target in result:
                result.add(p)
        for p, a, b in dod:
            if a in result and b in result:
                result.add(p)
        if len(result) == old_size:
            return result


def validate_closure(graph: DiGraph, seed: Sequence[int]) -> None:
    rows = graph.inevitable_rows()
    ntscd = graph.ntscd(rows)
    compact = graph.compact_dod(rows)
    explicit = graph.explicit_dod(compact)
    got = graph.rooted_dependency_closure(seed, ntscd, compact)
    expected = explicit_closure(graph.n, seed, ntscd, explicit)
    if got != expected:
        raise AssertionError(
            "closure mismatch\n"
            f"adjacency={graph.succ}\n"
            f"seed={list(seed)}\n"
            f"got={sorted(got)}\n"
            f"expected={sorted(expected)}"
        )


def exhaustive_four_vertices() -> int:
    n = 4
    total = 1 << (n * n)
    for mask in range(total):
        validate_dod(graph_from_mask(n, mask))
    return total


def randomized_dod(
    rng: random.Random,
    n: int,
    count: int,
    max_outdegree: Optional[int],
) -> None:
    for _ in range(count):
        validate_dod(random_graph(rng, n, max_outdegree))


def randomized_closure(rng: random.Random, count_per_size: int) -> int:
    total = 0
    for n in range(1, 9):
        for _ in range(count_per_size):
            graph = random_graph(rng, n, None if rng.randrange(2) else 2)
            seed = [v for v in range(n) if rng.randrange(2)]
            validate_closure(graph, seed)
            total += 1
    return total


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--quick",
        action="store_true",
        help="run a smaller randomized suite and skip exhaustive four-vertex enumeration",
    )
    parser.add_argument("--seed", type=int, default=0xC0D0D)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    started = time.perf_counter()
    records: List[str] = []

    if not args.quick:
        total = exhaustive_four_vertices()
        records.append(f"exhaustive arbitrary n=4 passed {total}")

        sparse_plan = [(5, 10_000), (6, 10_000), (7, 3_000)]
        dense_plan = [(5, 10_000), (6, 5_000), (7, 2_000)]
        closure_count = 2_000
    else:
        sparse_plan = [(5, 500), (6, 500), (7, 200)]
        dense_plan = [(5, 500), (6, 250), (7, 100)]
        closure_count = 100

    for n, count in sparse_plan:
        randomized_dod(rng, n, count, 2)
        records.append(f"random passed {n} {count} 2")

    for n, count in dense_plan:
        randomized_dod(rng, n, count, None)
        records.append(f"random passed {n} {count} None")

    total_closure = randomized_closure(rng, closure_count)
    records.append(f"closure random passed {total_closure}")
    records.append(f"seed {args.seed}")
    records.append(f"elapsed_seconds {time.perf_counter() - started:.3f}")

    print("\n".join(records))


if __name__ == "__main__":
    main()
