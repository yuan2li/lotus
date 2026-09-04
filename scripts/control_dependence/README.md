# Control-dependence tooling

This directory contains evaluation, benchmarking, and artifact generation
scripts for Lotus control-dependence analysis.

## Scripts

| Script | Purpose |
| --- | --- |
| `evaluate_control_dependence.py` | Driver orchestration for reproducible multi-benchmark evaluation (RQ1 & RQ2). Handles warmups, randomized run orders, repetitions, cross-variant output checks, and CSV metrics emission. |
| `synthetic_family.py` | Generator and theoretical validator for the Proposition 5.1 synthetic graph family. Emits LLVM IR (`.ll`) benchmarks exhibiting cubic output ($K = k^3$) with quadratic bicliques ($C = 2k^2$). |
| `generate_paper_artifacts.py` | Post-processing pipeline. Reads `summary.csv`, aggregates statistics, and automatically generates LaTeX macros (`paper_macros.tex`), subjects table (`tab_subjects.tex`), and TikZ figures (`fig_rq1_results.tikz`, `fig_rq1_closure.tikz`). |
| `sweep_closure_seeds.py` | Sweeps the closure seed-set size `|W|` over the `closure_k*.ll` family with randomly drawn seeds, reporting the speedup distribution rather than one seeding. Emits `closure_seed_sweep{,_raw}.csv`. |

## Usage Examples

```bash
# 1. Generate or benchmark the Proposition 5.1 synthetic family
python3 scripts/control_dependence/synthetic_family.py --benchmark
python3 scripts/control_dependence/synthetic_family.py --generate-suite

# 2. Run full control-dependence evaluation across benchmarks
python3 scripts/control_dependence/evaluate_control_dependence.py \
  benchmarks/real-world/SPEC2006 benchmarks/synthetic \
  --tool=build-release/bin/lotus-ir-control-dependence \
  --repeat=10 --warmup=2

# 3. Post-process results and generate paper LaTeX artifacts
python3 scripts/control_dependence/generate_paper_artifacts.py

# 4. Sweep the closure seed-set size with randomly drawn seeds
python3 scripts/control_dependence/sweep_closure_seeds.py --sizes 1 2 4 8 16 32 64 --trials 10
```

## Running the control-dependence unit tests

The unit tests live in `tests/unit/Analysis/ControlDependence/` and are guarded
by `LOTUS_BUILD_TESTS`, which is **off** in the usual evaluation build. Two of
them are relevant to the paper's closure claim:

- `StrongAndCompactClosureAgreeOnReachableGraphs` exhaustively checks that the
  dg baseline and the compact closure return the same set on every four-vertex
  graph whose vertices are all reachable from the start, over every seed set
  containing the start. It asserts that the sweep actually reaches a non-empty
  order relation, so it cannot pass vacuously on graphs where DOD is empty.
- `StrongClosureMissesUnreachableDecisions` pins the boundary: without the
  reachable-start hypothesis the two legitimately diverge, because a forward
  walk from the seed cannot see an unreachable decision.

Build them in a **separate** directory so the evaluation build's configuration
is left untouched:

```bash
cmake -S . -B build-tests -DCMAKE_BUILD_TYPE=Release -DLOTUS_BUILD_TESTS=ON
cmake --build build-tests --target control_dependence_tests
./build-tests/bin/tests/control_dependence_tests
```

To reuse the already-compiled objects in `build-release` instead, toggle the
flag and **restore it afterwards**, since leaving it on changes that build's
configuration:

```bash
cmake -DLOTUS_BUILD_TESTS=ON build-release
cmake --build build-release --target control_dependence_tests
./build-release/bin/tests/control_dependence_tests
cmake -DLOTUS_BUILD_TESTS=OFF build-release   # restore
```

The exhaustive closure test takes roughly 5 s; the rest of the suite is
under 100 ms.
