# Control-dependence tooling

This directory contains evaluation, benchmarking, and artifact generation
scripts for Lotus control-dependence analysis.

## Scripts

| Script | Purpose |
| --- | --- |
| `evaluate_control_dependence.py` | Driver orchestration for reproducible multi-benchmark evaluation (RQ1 & RQ2). Handles warmups, randomized run orders, repetitions, cross-variant output checks, and CSV metrics emission. |
| `compact_dod_reference.py` | Python reference implementation of compact NTSCD, the biclique DOD construction (with the inevitable-set precheck), rooted dependency closure, and a definition-level DOD checker. Favors clarity over speed; `synthetic_family.py` builds on it. |
| `validate_compact_dod.py` | Regression harness for the reference implementation: exhausts all four-vertex digraphs, compares compact DOD with the definition-level checker on random larger graphs, and compares compact closure with explicit fixed-point closure. `--quick` runs a smaller suite. |
| `synthetic_family.py` | Generator and theoretical validator for the Proposition 5.1 synthetic graph family. Emits LLVM IR (`.ll`) benchmarks exhibiting cubic output ($K = k^3$) with quadratic bicliques ($C = 2k^2$). `--benchmark` writes `synthetic_benchmark_results.csv` to `--results-dir` (default `control-dependence-synthetic-results/`). |
| `generate_paper_artifacts.py` | Post-processing pipeline. Reads `summary.csv`, aggregates statistics, and automatically generates LaTeX macros (`paper_macros.tex`, plus `closure_guard_macros.tex` comparing Full-Closure with both prior closures, guarded and unguarded, `closure_cav21_family_macros.tex` with `tab_closure_cav21_{family,sweep}.tex` for the non-empty family against the CAV'21 closure, and `shared_inevitability_macros.tex` comparing both RQ1 tasks after a shared inevitability matrix), tables (closure family, seed sweep, ablations, reducibility guard), and a three-panel TikZ figure (`fig_rq1_results.tikz`: enumeration and closure scatters plus the K/C bars). |
| `sweep_closure_seeds.py` | Sweeps the closure seed-set size `|W|` over the `closure_k*.ll` family with randomly drawn seeds, reporting the speedup distribution rather than one seeding. `--sota-algorithm ntscd-dod-closure` swaps Danicic et al.'s closure for Chalupa et al.'s NTSCD and DOD closure. Emits `closure_seed_sweep{,_raw}.csv`. |
| `function_size_distribution.py` | Re-runs the Full variants over the evaluated real-world subjects and describes the per-function CFG size distribution: size percentiles and, per size bucket, the share of functions, vertices, and Full-Enumerate/Full-Closure analysis time. Emits `function_sizes.csv`. |
| `random_graph_family.py` | Generates the random graphs of the CAV'21 DOD evaluation (500 vertices, 50 to 1000 edges, out-degree at most two, 10 graphs per edge count) as LLVM IR, both whole (`random_m*.ll`, for enumeration) and restricted to the part reachable from the start (`random_rooted_m*.ll`, for rooted closure). `--stats` writes per-graph size, decision, biclique, and triple counts and whether the reducibility guard holds to `random_graph_stats.csv`. |
| `svcomp_corpus.py` | Rebuilds the SV-COMP 2020 function corpus of the CAV'21 DOD evaluation (`sv-benchmarks` tag `svcomp20`): compiles every source file, measures each function with the driver, keeps those with at least 100 basic blocks, removes duplicates, and reports the functions whose DOD relation is nonempty. Emits `corpus_functions.csv` and `corpus_summary.json`. |

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

# 5. Closure against Chalupa et al.'s NTSCD and DOD closure (CAV'21), real
#    subjects with and without the reducibility guard, then the non-empty family
python3 scripts/control_dependence/evaluate_control_dependence.py \
  ../coreutils benchmarks/real-world/SPEC2006 benchmarks/synthetic ../open \
  --experiments rq1-closure,rq1-closure-cav21,rq1-closure-cav21-guarded-sota,rq1-closure-cav21-guarded-both \
  --repeat 5 --warmup 1 --timeout 300 --keep-going \
  --output-dir control-dependence-closure-guard-results
python3 scripts/control_dependence/evaluate_control_dependence.py \
  benchmarks/synthetic/closure_k*.ll --experiments rq1-closure,rq1-closure-cav21 \
  --repeat 5 --warmup 1 --seed-count 4 --output-dir control-dependence-closure-cav21-family
python3 scripts/control_dependence/sweep_closure_seeds.py --sizes 1 2 4 8 16 32 64 --trials 10 \
  --sota-algorithm ntscd-dod-closure --output-dir control-dependence-closure-cav21-family

# 6. Regenerate the artifacts with the CAV'21 closure behind the \ClosureSpeedup*
#    macros and figure panel (b), instead of Danicic et al.'s strong closure
python3 scripts/control_dependence/generate_paper_artifacts.py --closure-baseline cav21

# 7. Shared preprocessing: the SOTA side runs on a copy of the compact
#    inevitability matrix and both sides are compared after it
python3 scripts/control_dependence/evaluate_control_dependence.py \
  ../coreutils benchmarks/real-world/SPEC2006 benchmarks/synthetic ../open \
  --experiments rq1-enumeration,rq1-enumeration-guarded-both,rq1-enumeration-shared,rq1-enumeration-shared-guarded-both,rq1-closure-cav21,rq1-closure-cav21-guarded-both,rq1-closure-cav21-shared,rq1-closure-cav21-shared-guarded-both \
  --repeat 5 --warmup 1 --timeout 300 --keep-going \
  --output-dir control-dependence-shared-results

# 8. Paper artifacts with the CAV'21 closure and shared preprocessing behind the
#    real-subject speedups and figure panels (a) and (b); also writes the
#    combined scaling table (output sensitivity + closure family)
python3 scripts/control_dependence/generate_paper_artifacts.py \
  --closure-baseline cav21 --shared-inevitability
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

These tests are part of Lotus's shared `analysis_tests` suite, so filter to
`ControlDependenceTest.*` when running them. Build them in a **separate**
directory so the evaluation build's configuration is left untouched:

```bash
cmake -S . -B build-tests -DCMAKE_BUILD_TYPE=Release -DLOTUS_BUILD_TESTS=ON
cmake --build build-tests --target analysis_tests
./build-tests/bin/tests/analysis_tests --gtest_filter='ControlDependenceTest.*'
```

To reuse the already-compiled objects in `build-release` instead, toggle the
flag and **restore it afterwards**, since leaving it on changes that build's
configuration:

```bash
cmake -DLOTUS_BUILD_TESTS=ON build-release
cmake --build build-release --target analysis_tests
./build-release/bin/tests/analysis_tests --gtest_filter='ControlDependenceTest.*'
cmake -DLOTUS_BUILD_TESTS=OFF build-release   # restore
```

The exhaustive closure test takes roughly 5 s; the other
`ControlDependenceTest` cases take under 100 ms in total.
