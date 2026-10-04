#!/usr/bin/env python3
"""Random-graph inputs following the CAV'21 DOD evaluation (Section 6.2).

Chalupa et al. test their DOD algorithm on random graphs with 500 vertices and
50, 100, 150, ... randomly distributed edges such that every vertex has at most
two successors, averaging 10 graphs per edge count. This script generates the
same family as LLVM IR so the C++ driver can run every algorithm on it.

Edges are drawn one at a time: the source is uniform over the vertices that
still have fewer than two successors, the target uniform over the other
vertices that are not yet its successors. Self-loops and parallel edges are
excluded. A vertex with no successor returns, one with a single successor
branches unconditionally, and one with two branches on a function argument.

Each edge count becomes one module with one function per graph, in two forms:

``random_m<edges>.ll`` (full)
    All vertices, behind an entry block that jumps to vertex 0. LLVM forbids
    predecessors of the entry block, so the entry is a separate block. This
    is the CAV'21 setting and is used for DOD enumeration.
``random_rooted_m<edges>.ll`` (rooted)
    Only the vertices reachable from vertex 0. The NTSCD and DOD relations
    of a decision depend only on what its successors reach, so every kept
    decision has exactly the relation it had in the full graph, and the start
    now reaches every vertex as rooted strong closure requires.

``--stats`` runs the driver on the generated modules and writes one row per
graph with its size, decision count, biclique and triple counts, and whether
the reducibility guard holds.
"""

from __future__ import annotations

import argparse
import csv
import io
import random
import subprocess
import sys
from pathlib import Path

LOTUS_ROOT = Path(__file__).resolve().parents[2]


def random_graph(vertices: int, edges: int, rng: random.Random) -> list[list[int]]:
    """Return successor lists with at most two successors per vertex."""
    if edges > 2 * vertices:
        raise ValueError(f"{edges} edges exceed out-degree two on {vertices} vertices")
    successors: list[list[int]] = [[] for _ in range(vertices)]
    open_sources = list(range(vertices))
    for _ in range(edges):
        while True:
            source = rng.choice(open_sources)
            target = rng.randrange(vertices - 1)
            target += target >= source  # skip the source itself
            if target not in successors[source]:
                break
        successors[source].append(target)
        if len(successors[source]) == 2:
            open_sources.remove(source)
    return successors


def reachable(successors: list[list[int]], start: int) -> list[int]:
    """Vertices reachable from start, in discovery order (start first)."""
    seen = {start}
    order = [start]
    for vertex in order:
        for target in successors[vertex]:
            if target not in seen:
                seen.add(target)
                order.append(target)
    return order


def function_ir(name: str, successors: list[list[int]], keep: list[int]) -> str:
    """One LLVM function whose blocks are the kept vertices, entry first."""
    kept = set(keep)
    lines = [f"define void @{name}(i1 %c) {{", "entry:", f"  br label %v{keep[0]}"]
    for vertex in keep:
        targets = [t for t in successors[vertex] if t in kept]
        # Rooted graphs keep every successor of a kept vertex by construction.
        assert len(targets) == len(successors[vertex])
        lines.append(f"v{vertex}:")
        if not targets:
            lines.append("  ret void")
        elif len(targets) == 1:
            lines.append(f"  br label %v{targets[0]}")
        else:
            lines.append(f"  br i1 %c, label %v{targets[0]}, label %v{targets[1]}")
    lines.append("}")
    return "\n".join(lines)


def generate(args: argparse.Namespace) -> list[Path]:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for edges in args.edges:
        full, rooted = [], []
        for graph in range(args.graphs):
            # One stream per (edge count, graph) so any configuration can be
            # regenerated on its own.
            rng = random.Random(f"{args.seed}:{args.vertices}:{edges}:{graph}")
            successors = random_graph(args.vertices, edges, rng)
            name = f"g{graph}"
            full.append(function_ir(name, successors, list(range(args.vertices))))
            rooted.append(function_ir(name, successors, reachable(successors, 0)))
        for stem, functions, note in (
            (f"random_m{edges}", full, "all vertices; entry jumps to vertex 0"),
            (f"random_rooted_m{edges}", rooted, "only the vertices reachable from vertex 0"),
        ):
            path = args.output_dir / f"{stem}.ll"
            header = [
                f"; ModuleID = '{stem}.ll'",
                f"; CAV'21-style random graphs: {args.vertices} vertices, {edges} edges,",
                f"; out-degree at most two, {args.graphs} graphs, seed {args.seed}; {note}.",
                "",
            ]
            path.write_text("\n".join(header) + "\n\n".join(functions) + "\n")
            written.append(path)
    return written


def stats(args: argparse.Namespace) -> None:
    rows = []
    for edges in args.edges:
        for form, stem in (("full", f"random_m{edges}"), ("rooted", f"random_rooted_m{edges}")):
            path = args.output_dir / f"{stem}.ll"
            command = [str(args.tool), str(path), "--algorithm=dod-compact",
                       "--format=csv", "--reducibility-guard"]
            guarded = {r["function"]: r["guard_skipped"] for r in csv.DictReader(io.StringIO(
                subprocess.run(command, capture_output=True, text=True, check=True).stdout))}
            command = [str(args.tool), str(path), "--algorithm=dod-compact", "--format=csv"]
            for r in csv.DictReader(io.StringIO(
                    subprocess.run(command, capture_output=True, text=True, check=True).stdout)):
                pairs, incidences = int(r["dod_pairs"]), int(r["incidences"])
                rows.append({
                    "edges": edges, "form": form, "graph": r["function"],
                    # The entry block is not one of the random vertices.
                    "vertices": int(r["nodes"]) - 1, "decisions": int(r["decisions"]),
                    "bicliques": int(r["bicliques"]), "incidences": incidences,
                    "dod_pairs": pairs, "nonempty_dod": int(pairs > 0),
                    "k_over_c": pairs / incidences if incidences else 0.0,
                    "guard_skipped": int(guarded[r["function"]]),
                })
    target = args.output_dir / "random_graph_stats.csv"
    with target.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Written: {target}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vertices", type=int, default=500)
    parser.add_argument("--edges", type=int, nargs="+",
                        default=list(range(50, 1001, 50)),
                        help="edge counts (default 50, 100, ..., 1000)")
    parser.add_argument("--graphs", type=int, default=10, help="graphs per edge count")
    parser.add_argument("--seed", type=int, default=2021)
    parser.add_argument("--output-dir", type=Path,
                        default=LOTUS_ROOT / "benchmarks" / "random")
    parser.add_argument("--stats", action="store_true",
                        help="also run the driver and write random_graph_stats.csv")
    parser.add_argument("--tool", type=Path,
                        default=LOTUS_ROOT / "build-release/bin/lotus-ir-control-dependence")
    args = parser.parse_args()

    written = generate(args)
    print(f"Written {len(written)} modules to {args.output_dir}", file=sys.stderr)
    if args.stats:
        stats(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
