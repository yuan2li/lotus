#!/usr/bin/env python3
"""Sweep closure seed-set size |W| over the non-trivial-closure family.

The single-configuration closure result is sensitive to how the seeds are
placed. An even spread (``--seed-count`` alone) can align with a benchmark's
block layout and land near the best case, so this sweep reports the
distribution over randomly drawn seed sets instead of one favourable point.

For each ``closure_k*.ll`` and each |W|, the harness runs ``--trials``
independent draws (distinct ``--seed-rng`` values) of SOTA-Closure and
Full-Closure, checks that they agree, and records the speedup distribution.

Outputs ``closure_seed_sweep.csv`` (one row per instance/|W|) plus a
``closure_seed_sweep_raw.csv`` with every trial.
"""

from __future__ import annotations

import argparse
import csv
import io
import statistics
import subprocess
import sys
from pathlib import Path

LOTUS_ROOT = Path(__file__).resolve().parents[2]


def run(tool: Path, path: Path, algorithm: str, seed_count: int, rng: int,
        repeat: int, timeout: float) -> tuple[int, int, int]:
    """Return (best analysis_ns, closure_size, fingerprint) over `repeat` runs."""
    best = None
    for _ in range(repeat):
        command = [
            str(tool), str(path), f"--algorithm={algorithm}", "--format=csv",
            f"--seed-count={seed_count}",
        ]
        if rng:
            command.append(f"--seed-rng={rng}")
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=timeout)
        if completed.returncode != 0:
            raise RuntimeError(f"driver failed: {' '.join(command)}")
        row = next(iter(csv.DictReader(io.StringIO(completed.stdout))))
        value = int(row["analysis_ns"])
        if best is None or value < best[0]:
            best = (value, int(row["closure_size"]), int(row["result_fingerprint"]))
    assert best is not None
    return best


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="*", type=Path,
                        help="closure_k*.ll files (default: benchmarks/synthetic)")
    parser.add_argument("--tool", type=Path,
                        default=LOTUS_ROOT / "build-release/bin/lotus-ir-control-dependence")
    parser.add_argument("--sizes", type=int, nargs="+",
                        default=[1, 2, 4, 8, 16, 32, 64],
                        help="seed-set sizes |W| to sweep")
    parser.add_argument("--trials", type=int, default=10,
                        help="independent random draws per (instance, |W|)")
    parser.add_argument("--repeat", type=int, default=3,
                        help="timed runs per draw; the minimum is kept")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--output-dir", type=Path,
                        default=LOTUS_ROOT / "control-dependence-results-closure")
    args = parser.parse_args()

    inputs = args.inputs or sorted(
        (LOTUS_ROOT / "benchmarks" / "synthetic").glob("closure_k*.ll"),
        key=lambda p: int(p.stem.replace("closure_k", "")),
    )
    if not inputs:
        print("error: no closure_k*.ll inputs found", file=sys.stderr)
        return 2

    raw_rows: list[dict[str, object]] = []
    summary_rows: list[dict[str, object]] = []

    for path in inputs:
        k = int(path.stem.replace("closure_k", ""))
        for size in args.sizes:
            speedups: list[float] = []
            closures: list[int] = []
            mismatches = 0
            for trial in range(args.trials):
                # rng 0 would mean "even spread"; offset so every draw is random.
                rng = 1000 * size + trial + 1
                sota = run(args.tool, path, "strong-closure", size, rng,
                           args.repeat, args.timeout)
                full = run(args.tool, path, "compact-closure", size, rng,
                           args.repeat, args.timeout)
                agree = sota[1] == full[1] and sota[2] == full[2]
                mismatches += 0 if agree else 1
                speedup = sota[0] / full[0] if full[0] else float("nan")
                speedups.append(speedup)
                closures.append(full[1])
                raw_rows.append({
                    "benchmark": path.name, "k": k, "seed_count": size,
                    "trial": trial, "rng": rng,
                    "sota_ns": sota[0], "full_ns": full[0],
                    "closure_size": full[1], "outputs_match": agree,
                    "speedup": speedup,
                })
            speedups.sort()
            summary_rows.append({
                "benchmark": path.name, "k": k, "seed_count": size,
                "trials": args.trials,
                "closure_median": statistics.median(closures),
                "closure_min": min(closures), "closure_max": max(closures),
                "speedup_median": statistics.median(speedups),
                "speedup_min": min(speedups), "speedup_max": max(speedups),
                "speedup_stdev": statistics.stdev(speedups) if len(speedups) > 1 else 0.0,
                "outputs_match": mismatches == 0,
            })
            print(f"{path.name:18} |W|={size:<3} "
                  f"closure {min(closures):>4}-{max(closures):<4} "
                  f"speedup median={statistics.median(speedups):8.1f}x "
                  f"[{min(speedups):.1f}, {max(speedups):.1f}] "
                  f"{'OK' if mismatches == 0 else f'{mismatches} MISMATCH'}",
                  file=sys.stderr)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("closure_seed_sweep.csv", summary_rows),
                       ("closure_seed_sweep_raw.csv", raw_rows)):
        target = args.output_dir / name
        with target.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        print(f"Written: {target}")

    bad = [r for r in summary_rows if not r["outputs_match"]]
    if bad:
        print(f"warning: {len(bad)} configuration(s) disagreed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
