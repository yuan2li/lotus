# Control-dependence tooling

This directory contains evaluation, benchmarking, and artifact generation
scripts for Lotus control-dependence analysis.

## Scripts

| Script | Purpose |
| --- | --- |
| `evaluate_control_dependence.py` | Driver orchestration for reproducible multi-benchmark evaluation (RQ1 & RQ2). Handles warmups, randomized run orders, repetitions, cross-variant output checks, and CSV metrics emission. |
| `synthetic_family.py` | Generator and theoretical validator for the Proposition 5.1 synthetic graph family. Emits LLVM IR (`.ll`) benchmarks exhibiting cubic output ($K = k^3$) with quadratic bicliques ($C = 2k^2$). |
| `generate_paper_artifacts.py` | Post-processing pipeline. Reads `summary.csv`, aggregates statistics, and automatically generates LaTeX macros (`paper_macros.tex`), subjects table (`tab_subjects.tex`), and TikZ figures (`fig_rq1_results.tikz`, `fig_rq1_closure.tikz`). |

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
```
