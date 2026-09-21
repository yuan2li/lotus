#!/usr/bin/env python3
"""Aggregate experiment CSVs and generate publication artifacts for the paper.

Reads summary.csv, raw.csv, and metadata.json from evaluation runs, computes
geometric means, quantiles, and paired statistics, and automatically generates:
1. paper_macros.tex: LaTeX macro definitions for abstract/intro/eval placeholders
2. tab_subjects.tex: Evaluation subjects table (Table 2)
3. fig_rq1_results.tikz: Real-data TikZ scatter & bar plots (Figure 3)
4. fig_rq1_closure.tikz: Real-data TikZ closure scatter plot (Figure 4)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence


def geometric_mean(values: Sequence[float]) -> float:
    """Compute the geometric mean of positive floats."""
    clean = [v for v in values if v > 0]
    if not clean:
        return 1.0
    return math.exp(sum(math.log(v) for v in clean) / len(clean))


def classify_suite(row: Dict[str, Any]) -> str:
    """Return the benchmark suite of a summary row: SPEC, coreutils, open, synthetic."""
    input_path = str(row.get("input", ""))
    name = str(row.get("benchmark", ""))
    if "synthetic" in input_path or "synthetic" in name:
        return "synthetic"
    if "/SPEC2006/" in input_path or "SPEC2006" in input_path:
        return "SPEC"
    if "/coreutils/" in input_path:
        return "coreutils"
    if "/open/" in input_path:
        return "open"
    # Fallback for older CSVs that carried only the benchmark filename.
    if name.split(".")[0].isdigit():
        return "SPEC"
    return "unknown"


def is_real_world(row: Dict[str, Any]) -> bool:
    return classify_suite(row) in {"SPEC", "coreutils", "open"}


def read_summary_csv(path: Path) -> List[Dict[str, Any]]:
    rows = []
    if not path.is_file():
        return rows
    with path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            parsed: Dict[str, Any] = dict(r)
            for key in (
                "functions", "nodes", "edges", "decisions",
                "reference_bicliques", "candidate_bicliques",
                "reference_incidences", "candidate_incidences",
                "reference_dod_pairs", "candidate_dod_pairs",
                "reference_output", "candidate_output",
                "reference_median_ns", "candidate_median_ns",
                "reference_over_candidate_time", "candidate_over_reference_time",
                "reference_median_peak_rss_kb", "candidate_median_peak_rss_kb",
                "candidate_over_reference_memory",
            ):
                if key in parsed and parsed[key] != "":
                    try:
                        parsed[key] = float(parsed[key]) if "." in parsed[key] else int(parsed[key])
                    except ValueError:
                        pass
            rows.append(parsed)
    return rows


def generate_paper_macros(summary_rows: List[Dict[str, Any]]) -> str:
    """Generate LaTeX macros to fill all XX placeholders in paper-control-dep."""
    # Split real-world benchmarks (SPEC + coreutils + open) vs synthetic
    spec_enum = [r for r in summary_rows if r.get("experiment") == "rq1-enumeration" and is_real_world(r)]
    spec_closure = [r for r in summary_rows if r.get("experiment") == "rq1-closure" and is_real_world(r)]
    rq2_card = [r for r in summary_rows if r.get("experiment") == "rq2-cardinality" and is_real_world(r)]
    rq2_cons = [r for r in summary_rows if r.get("experiment") == "rq2-consumption" and is_real_world(r)]

    # Total counts
    total_funcs = sum(int(r.get("functions", 0)) for r in spec_enum)
    total_nodes = sum(int(r.get("nodes", 0)) for r in spec_enum)
    total_edges = sum(int(r.get("edges", 0)) for r in spec_enum)

    # Enumeration metrics
    enum_speedups = [float(r["reference_over_candidate_time"]) for r in spec_enum if "reference_over_candidate_time" in r]
    enum_geomean = geometric_mean(enum_speedups) if enum_speedups else 1.0
    enum_max_speedup = max(enum_speedups) if enum_speedups else 1.0
    enum_max_subject = spec_enum[enum_speedups.index(enum_max_speedup)]["benchmark"].replace(".bc", "") if enum_speedups else "h264ref"

    enum_mem_reductions = [
        (1.0 - float(r["candidate_median_peak_rss_kb"]) / float(r["reference_median_peak_rss_kb"])) * 100.0
        for r in spec_enum
        if float(r.get("reference_median_peak_rss_kb", 0)) > 0
    ]
    enum_mem_reduction_mean = (
        sum(enum_mem_reductions) / len(enum_mem_reductions) if enum_mem_reductions else 0.0
    )
    # The peak-RSS difference is whole-process and turns out to be within its own
    # run-to-run spread, so the paper reports it as "no measurable difference"
    # rather than as a reduction. These macros keep that statement data-driven.
    import statistics as _stats

    enum_mem_stdev = (
        _stats.stdev(enum_mem_reductions) if len(enum_mem_reductions) > 1 else 0.0
    )
    enum_mem_worse = sum(1 for value in enum_mem_reductions if value < 0)
    enum_mem_total = len(enum_mem_reductions)

    # Compression ratio K/C: evaluated on subjects with non-empty order relations
    all_enum = [r for r in summary_rows if r.get("experiment") == "rq1-enumeration"]
    compression_data = []
    for r in all_enum:
        k = float(r.get("candidate_dod_pairs", 0))
        c = float(r.get("candidate_incidences", 0))
        if c > 0 and k > 0:
            name = r["benchmark"].replace(".ll", "").replace(".bc", "")
            compression_data.append((name, k / c))

    compression_ratios = [ratio for _, ratio in compression_data]
    compression_median = (
        sorted(compression_ratios)[len(compression_ratios) // 2]
        if compression_ratios
        else 1.0
    )
    compression_max = max(compression_ratios) if compression_ratios else 1.0
    compression_max_subject = (
        sorted(compression_data, key=lambda x: x[1])[-1][0]
        if compression_data
        else "synthetic_k50"
    )

    # Closure metrics
    closure_speedups = [float(r["reference_over_candidate_time"]) for r in spec_closure if "reference_over_candidate_time" in r]
    closure_geomean = geometric_mean(closure_speedups) if closure_speedups else 1.0
    closure_max_speedup = max(closure_speedups) if closure_speedups else 1.0
    closure_max_subject = spec_closure[closure_speedups.index(closure_max_speedup)]["benchmark"].replace(".bc", "") if closure_speedups else "cactusADM"

    closure_mem_reductions = [
        (1.0 - float(r["candidate_median_peak_rss_kb"]) / float(r["reference_median_peak_rss_kb"])) * 100.0
        for r in spec_closure
        if float(r.get("reference_median_peak_rss_kb", 0)) > 0
    ]
    closure_mem_reduction_mean = (
        sum(closure_mem_reductions) / len(closure_mem_reductions) if closure_mem_reductions else 0.0
    )
    # The reduction means above are reported as magnitudes, so also emit the
    # direction-aware figures the prose relies on: how much more memory the
    # biclique variants ever use, and how many closure subjects are slower.
    enum_mem_max_increase = max([0.0] + [-value for value in enum_mem_reductions])
    closure_mem_increase_mean = -closure_mem_reduction_mean
    closure_mem_worse = sum(1 for value in closure_mem_reductions if value < 0)
    closure_mem_max_increase = max([0.0] + [-value for value in closure_mem_reductions])
    mem_max_increase = max(enum_mem_max_increase, closure_mem_max_increase)
    if closure_mem_increase_mean < 0:
        print("Warning: Full-Closure uses less peak memory on average; "
              "the evaluation prose says it rose.", file=sys.stderr)

    closure_slower = [
        r for r in spec_closure
        if "reference_over_candidate_time" in r and float(r["reference_over_candidate_time"]) < 1.0
    ]
    closure_slower_max_ms = max(
        [0.0] + [max(float(r["reference_median_ns"]), float(r["candidate_median_ns"])) / 1e6
                 for r in closure_slower]
    )

    # Ablation metrics
    exact_set_overhead = [float(r["candidate_over_reference_time"]) for r in rq2_card if "candidate_over_reference_time" in r]
    exact_set_geomean = geometric_mean(exact_set_overhead) if exact_set_overhead else 1.0

    eager_pairs_overhead = [float(r["candidate_over_reference_time"]) for r in rq2_cons if "candidate_over_reference_time" in r]
    eager_pairs_geomean = geometric_mean(eager_pairs_overhead) if eager_pairs_overhead else 1.0

    # Spreads make the null result on real subjects checkable: both ablations
    # stay within run-to-run noise there because the order relation is empty.
    exact_set_min = min(exact_set_overhead) if exact_set_overhead else 1.0
    exact_set_max = max(exact_set_overhead) if exact_set_overhead else 1.0
    eager_pairs_min = min(eager_pairs_overhead) if eager_pairs_overhead else 1.0
    eager_pairs_max = max(eager_pairs_overhead) if eager_pairs_overhead else 1.0

    clean_comp_subject = compression_max_subject.replace("_", "\\_")
    clean_enum_subject = enum_max_subject.replace("_", "\\_")
    clean_closure_subject = closure_max_subject.replace("_", "\\_")

    macros = [
        "% Auto-generated paper evaluation macros by generate_paper_artifacts.py",
        f"\\newcommand{{\\TotalSubjectCount}}{{{len(spec_enum)}}}",
        f"\\newcommand{{\\TotalFunctionsCount}}{{{total_funcs:,}}}",
        f"\\newcommand{{\\TotalCFGVerticesCount}}{{{total_nodes:,}}}",
        f"\\newcommand{{\\TotalCFGEdgesCount}}{{{total_edges:,}}}",
        f"\\newcommand{{\\EnumSpeedupGeomean}}{{{enum_geomean:.1f}}}",
        f"\\newcommand{{\\EnumMaxSpeedup}}{{{enum_max_speedup:.1f}}}",
        f"\\newcommand{{\\EnumMaxSubject}}{{\\texttt{{{clean_enum_subject}}}}}",
        f"\\newcommand{{\\EnumMemReductionPercent}}{{{abs(enum_mem_reduction_mean):.1f}}}",
        f"\\newcommand{{\\EnumMemStdevPercent}}{{{enum_mem_stdev:.1f}}}",
        f"\\newcommand{{\\EnumMemWorseCount}}{{{enum_mem_worse}}}",
        f"\\newcommand{{\\EnumMemSubjectCount}}{{{enum_mem_total}}}",
        f"\\newcommand{{\\CompressionRatioMedian}}{{{compression_median:.1f}}}",
        f"\\newcommand{{\\CompressionRatioMax}}{{{compression_max:.1f}}}",
        f"\\newcommand{{\\CompressionMaxSubject}}{{\\texttt{{{clean_comp_subject}}}}}",
        f"\\newcommand{{\\ClosureSpeedupGeomean}}{{{closure_geomean:.1f}}}",
        f"\\newcommand{{\\ClosureSpeedupMax}}{{{closure_max_speedup:.1f}}}",
        f"\\newcommand{{\\ClosureMaxSubject}}{{\\texttt{{{clean_closure_subject}}}}}",
        f"\\newcommand{{\\ClosureMemReductionPercent}}{{{abs(closure_mem_reduction_mean):.1f}}}",
        f"\\newcommand{{\\EnumMemMaxIncreasePercent}}{{{enum_mem_max_increase:.1f}}}",
        f"\\newcommand{{\\ClosureMemIncreasePercent}}{{{closure_mem_increase_mean:.1f}}}",
        f"\\newcommand{{\\ClosureMemWorseCount}}{{{closure_mem_worse}}}",
        f"\\newcommand{{\\ClosureMemMaxIncreasePercent}}{{{closure_mem_max_increase:.1f}}}",
        f"\\newcommand{{\\MemMaxIncreasePercent}}{{{mem_max_increase:.1f}}}",
        f"\\newcommand{{\\ClosureSlowerCount}}{{{len(closure_slower)}}}",
        f"\\newcommand{{\\ClosureSlowerMaxMs}}{{{closure_slower_max_ms:.2f}}}",
        f"\\newcommand{{\\ExactSetOverheadGeomean}}{{{exact_set_geomean:.1f}}}",
        f"\\newcommand{{\\EagerPairsOverheadGeomean}}{{{eager_pairs_geomean:.1f}}}",
        f"\\newcommand{{\\ExactSetOverheadMin}}{{{exact_set_min:.2f}}}",
        f"\\newcommand{{\\ExactSetOverheadMax}}{{{exact_set_max:.2f}}}",
        f"\\newcommand{{\\EagerPairsOverheadMin}}{{{eager_pairs_min:.2f}}}",
        f"\\newcommand{{\\EagerPairsOverheadMax}}{{{eager_pairs_max:.2f}}}",
    ]
    return "\n".join(macros) + "\n"


def generate_subjects_table(summary_rows: List[Dict[str, Any]]) -> str:
    """Generate LaTeX source for Table 2 (Evaluation subjects and sizes).

    SPEC CPU2006 is enumerated per subject; the larger coreutils and open
    suites are collapsed into aggregated rows so the table stays legible.
    """
    real_enum = [r for r in summary_rows
                 if r.get("experiment") == "rq1-enumeration" and is_real_world(r)]

    def sum_int(rows: List[Dict[str, Any]], key: str) -> int:
        return sum(int(r.get(key, 0)) for r in rows)

    by_suite: Dict[str, List[Dict[str, Any]]] = {"SPEC": [], "coreutils": [], "open": []}
    for r in real_enum:
        by_suite.setdefault(classify_suite(r), []).append(r)

    lines = [
        "% Auto-generated subjects table for Table 2",
        "\\begin{tabular}{@{}llrrrr@{}}",
        "\\toprule",
        "Subject & Benchmark Suite & Funcs & $|V|$ & $|E|$ & Decisions \\\\",
        "\\midrule",
    ]
    for r in sorted(by_suite["SPEC"], key=lambda x: x["benchmark"]):
        clean = r["benchmark"].replace(".bc", "").replace(".ll", "")
        lines.append(
            f"\\texttt{{{clean}}} & SPEC CPU2006 & "
            f"{int(r.get('functions', 0)):,} & {int(r.get('nodes', 0)):,} & "
            f"{int(r.get('edges', 0)):,} & {int(r.get('decisions', 0)):,} \\\\"
        )
    for suite, label in (("coreutils", "GNU Coreutils"), ("open", "Open-source apps")):
        rows = by_suite[suite]
        if not rows:
            continue
        lines.append("\\midrule")
        lines.append(
            f"{len(rows)} programs & {label} & "
            f"{sum_int(rows, 'functions'):,} & {sum_int(rows, 'nodes'):,} & "
            f"{sum_int(rows, 'edges'):,} & {sum_int(rows, 'decisions'):,} \\\\"
        )

    total_funcs = sum_int(real_enum, "functions")
    total_nodes = sum_int(real_enum, "nodes")
    total_edges = sum_int(real_enum, "edges")
    total_decisions = sum_int(real_enum, "decisions")
    lines.extend([
        "\\midrule",
        f"\\textbf{{Total}} & \\textbf{{{len(real_enum)} subjects}} & "
        f"\\textbf{{{total_funcs:,}}} & \\textbf{{{total_nodes:,}}} & "
        f"\\textbf{{{total_edges:,}}} & \\textbf{{{total_decisions:,}}} \\\\",
        "\\bottomrule",
        "\\end{tabular}",
    ])
    return "\n".join(lines) + "\n"


def generate_figures_tikz(summary_rows: List[Dict[str, Any]]) -> tuple[str, str]:
    """Generate TikZ code for Figure 3 and Figure 4 with real data coordinates."""
    spec_enum = [r for r in summary_rows if r.get("experiment") == "rq1-enumeration" and is_real_world(r)]
    spec_closure = [r for r in summary_rows if r.get("experiment") == "rq1-closure" and is_real_world(r)]
    all_enum = [r for r in summary_rows if r.get("experiment") == "rq1-enumeration"]

    # Figure 5(a): Scatter plot of Enum time (log scale)
    all_enum_ref = [float(r["reference_median_ns"]) / 1e6 for r in spec_enum if float(r["reference_median_ns"]) > 0]
    all_enum_cand = [float(r["candidate_median_ns"]) / 1e6 for r in spec_enum if float(r["candidate_median_ns"]) > 0]

    min_val = min(min(all_enum_ref), min(all_enum_cand), 0.01)
    max_val = max(max(all_enum_ref), max(all_enum_cand), 100.0)
    log_min = math.log10(min_val)
    log_max = math.log10(max_val)
    log_span = max(log_max - log_min, 1.0)

    def to_coord(val_ms: float) -> float:
        l = math.log10(max(val_ms, min_val))
        return (l - log_min) / log_span * 2.2 + 0.3

    enum_points = []
    for r in spec_enum:
        ref_ms = float(r["reference_median_ns"]) / 1e6
        cand_ms = float(r["candidate_median_ns"]) / 1e6
        cx = to_coord(ref_ms)
        cy = to_coord(cand_ms)
        enum_points.append(f"    \\fill[blue!70!black] ({cx:.2f},{cy:.2f}) circle (1.5pt);")

    # Figure 5(b): Bar plot of K/C ratio across instances with non-empty order relations
    compression_items = []
    for r in all_enum:
        k = float(r.get("candidate_dod_pairs", 0))
        c = float(r.get("candidate_incidences", 0))
        if c > 0 and k > 0:
            clean_name = r["benchmark"].replace(".ll", "").replace(".bc", "").replace("synthetic_", "")
            compression_items.append((clean_name, k / c))

    compression_items.sort(key=lambda x: x[1])
    # Select representative instances to display cleanly on the x-axis
    selected_items = [
        item for item in compression_items
        if item[0] in ("k5", "k10", "k20", "k30", "k50")
    ]
    if not selected_items:
        selected_items = compression_items[-5:] if len(compression_items) >= 5 else compression_items

    bar_nodes = []
    max_r = max(r[1] for r in selected_items) if selected_items else 25.0
    bar_width = 0.32
    for idx, (name, ratio) in enumerate(selected_items):
        # Pitch must exceed the rendered label width: tikz `scale` shrinks
        # coordinates but not text, so a tight pitch collides once scaled.
        bx = 0.28 + idx * 0.68
        bh = max(ratio / max_r * 2.1, 0.15)
        color = f"blue!{35 + idx*14}"
        bar_nodes.append(f"    \\fill[{color}] ({bx:.2f},0) rectangle ({bx+bar_width:.2f},{bh:.2f});")
        bar_nodes.append(f"    \\node[above,font=\\tiny] at ({bx+bar_width/2:.2f},{bh:.2f}) {{{ratio:g}$\\times$}};")
        bar_nodes.append(f"    \\node[anchor=north,font=\\tiny] at ({bx+bar_width/2:.2f},-0.05) {{{name}}};")

    bar_nodes_str = chr(10).join(bar_nodes)

    fig3_tikz = f"""% Auto-generated Figure 5 TikZ code from real evaluation data
\\begin{{tikzpicture}}[font=\\scriptsize,>=Latex]
  \\begin{{scope}}
    \\draw[->] (0,0) -- (3.2,0);
    \\draw[->] (0,0) -- (0,2.8);
    \\draw[dashed,gray] (0.3,0.3) -- (2.6,2.6);
{chr(10).join(enum_points)}
    \\node[gray!80!black,font=\\scriptsize\\bfseries] at (1.1,2.2) {{\\EnumSpeedupGeomean$\\times$ geomean}};
    \\node[font=\\scriptsize] at (1.55,-0.45) {{SOTA-Enumerate time (ms)}};
    \\node[font=\\scriptsize,rotate=90] at (-0.45,1.4) {{Full-Enumerate time (ms)}};
    \\node at (1.55,-1.00) {{(a) enumeration-time scatter}};
  \\end{{scope}}
  \\begin{{scope}}[xshift=4.6cm]
    \\draw[help lines, gray!25, dashed] (0, 0.42) -- (3.5, 0.42);
    \\draw[help lines, gray!25, dashed] (0, 0.84) -- (3.5, 0.84);
    \\draw[help lines, gray!25, dashed] (0, 1.26) -- (3.5, 1.26);
    \\draw[help lines, gray!25, dashed] (0, 2.10) -- (3.5, 2.10);
    \\node[left,font=\\tiny,gray!80] at (0, 0.42) {{5$\\times$}};
    \\node[left,font=\\tiny,gray!80] at (0, 0.84) {{10$\\times$}};
    \\node[left,font=\\tiny,gray!80] at (0, 1.26) {{15$\\times$}};
    \\node[left,font=\\tiny,gray!80] at (0, 2.10) {{25$\\times$}};
    \\draw[->] (0,0) -- (3.7,0);
    \\draw[->] (0,0) -- (0,2.8);
{bar_nodes_str}
    \\node[gray!80!black,font=\\scriptsize\\bfseries] at (1.85,2.45) {{\\CompressionRatioMedian$\\times$ median}};
    \\node[font=\\scriptsize] at (1.85,-0.62) {{synthetic instances ($k$)}};
    \\node[font=\\scriptsize,rotate=90] at (-0.95,1.4) {{$K/C$ compression ratio}};
    \\node at (1.85,-1.00) {{(b) compression ratio}};
  \\end{{scope}}
\\end{{tikzpicture}}
"""

    # Figure 4: Closure time scatter plot
    all_cls_ref = [float(r["reference_median_ns"]) / 1e6 for r in spec_closure if float(r["reference_median_ns"]) > 0]
    all_cls_cand = [float(r["candidate_median_ns"]) / 1e6 for r in spec_closure if float(r["candidate_median_ns"]) > 0]
    c_min = min(min(all_cls_ref), min(all_cls_cand), 0.01)
    c_max = max(max(all_cls_ref), max(all_cls_cand), 300.0)
    c_log_min = math.log10(c_min)
    c_log_max = math.log10(c_max)
    c_span = max(c_log_max - c_log_min, 1.0)

    def to_cls_coord(val_ms: float) -> float:
        l = math.log10(max(val_ms, c_min))
        return (l - c_log_min) / c_span * 2.2 + 0.3

    closure_points = []
    for r in spec_closure:
        ref_ms = float(r["reference_median_ns"]) / 1e6
        cand_ms = float(r["candidate_median_ns"]) / 1e6
        cx = to_cls_coord(ref_ms)
        cy = to_cls_coord(cand_ms)
        closure_points.append(f"    \\fill[blue!70!black] ({cx:.2f},{cy:.2f}) circle (1.5pt);")

    fig4_tikz = f"""% Auto-generated Figure 4 TikZ code from real evaluation data
\\begin{{tikzpicture}}[font=\\scriptsize,>=Latex]
  \\begin{{scope}}
    \\draw[->] (0,0) -- (3.2,0);
    \\draw[->] (0,0) -- (0,2.8);
    \\draw[dashed,gray] (0.3,0.3) -- (2.6,2.6);
{chr(10).join(closure_points)}
    \\node[gray!80!black,font=\\scriptsize\\bfseries] at (1.1,2.2) {{\\ClosureSpeedupGeomean$\\times$ geomean}};
    \\node[font=\\scriptsize] at (1.6,-0.45) {{SOTA-Closure time (ms)}};
    \\node[font=\\scriptsize,rotate=90] at (-0.45,1.4) {{Full-Closure time (ms)}};
  \\end{{scope}}
\\end{{tikzpicture}}
"""
    return fig3_tikz, fig4_tikz


LOTUS_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = LOTUS_ROOT.parent


def generate_closure_family_artifacts(rows: List[Dict[str, Any]]) -> tuple[str, str, str]:
    """Macros and a table for the well-formed non-trivial-closure family.

    The real-world subjects all have an empty DOD relation, so they cannot
    exercise the biclique representation during closure.  This family does: it
    keeps K = k^3 and C = 2k^2 while making every decision reachable, so the
    rooted closure is non-trivial (2k+2) and the cubic-vs-quadratic separation
    becomes measurable end to end.
    """
    def k_of(row: Dict[str, Any]) -> int:
        name = str(row["benchmark"])
        return int(name.replace("closure_k", "").replace(".ll", ""))

    closure = sorted(
        (r for r in rows if r.get("experiment") == "rq1-closure"), key=k_of
    )
    enum = {k_of(r): r for r in rows if r.get("experiment") == "rq1-enumeration"}
    eager = {k_of(r): r for r in rows if r.get("experiment") == "rq2-consumption"}

    speedups = [float(r["reference_over_candidate_time"]) for r in closure]
    geo = geometric_mean(speedups)
    max_speedup = max(speedups)
    max_k = k_of(max(closure, key=lambda r: float(r["reference_over_candidate_time"])))
    eager_overheads = [float(r["candidate_over_reference_time"]) for r in eager.values()]
    exact = {k_of(r): r for r in rows if r.get("experiment") == "rq2-cardinality"}
    exact_overheads = [float(r["candidate_over_reference_time"]) for r in exact.values()]

    macros = [
        "% Auto-generated closure-family macros by generate_paper_artifacts.py",
        f"\\newcommand{{\\ClosureFamilyCount}}{{{len(closure)}}}",
        f"\\newcommand{{\\ClosureFamilyMinK}}{{{k_of(closure[0])}}}",
        f"\\newcommand{{\\ClosureFamilyMaxK}}{{{k_of(closure[-1])}}}",
        f"\\newcommand{{\\ClosureFamilySpeedupGeomean}}{{{geo:.0f}}}",
        f"\\newcommand{{\\ClosureFamilySpeedupMax}}{{{max_speedup:.0f}}}",
        f"\\newcommand{{\\ClosureFamilySpeedupMaxK}}{{{max_k}}}",
        f"\\newcommand{{\\ClosureFamilySpeedupMin}}{{{min(speedups):.1f}}}",
        f"\\newcommand{{\\ClosureFamilyEagerOverheadMax}}{{{max(eager_overheads):.1f}}}",
        f"\\newcommand{{\\ClosureFamilyEagerOverheadMin}}{{{min(eager_overheads):.1f}}}",
        f"\\newcommand{{\\ClosureFamilyExactSetOverheadMax}}{{{max(exact_overheads):.2f}}}",
        f"\\newcommand{{\\ClosureFamilyExactSetOverheadMin}}{{{min(exact_overheads):.2f}}}",
        f"\\newcommand{{\\ClosureFamilyMaxTriples}}{{{int(closure[-1]['candidate_dod_pairs']):,}}}",
        f"\\newcommand{{\\ClosureFamilyMaxClosure}}{{{int(closure[-1]['candidate_output'])}}}",
    ]

    lines = [
        "% Auto-generated closure-family table",
        "\\begin{tabular}{@{}rrrrrrr@{}}",
        "\\toprule",
        "$k$ & $K$ & $C$ & $|W'|$ & SOTA (ms) & Full (ms) & Speedup \\\\",
        "\\midrule",
    ]
    for r in closure:
        k = k_of(r)
        e = enum[k]
        lines.append(
            f"{k} & {int(e['candidate_dod_pairs']):,} & {int(e['candidate_incidences']):,} & "
            f"{int(r['candidate_output'])} & "
            f"{float(r['reference_median_ns']) / 1e6:.2f} & "
            f"{float(r['candidate_median_ns']) / 1e6:.2f} & "
            f"{float(r['reference_over_candidate_time']):.0f}$\\times$ \\\\"
        )
    lines += ["\\bottomrule", "\\end{tabular}"]

    ablation = [
        "% Auto-generated ablation table for the non-empty family",
        "\\begin{tabular}{@{}rrrrrr@{}}",
        "\\toprule",
        "$k$ & \\emph{Full-Build} & \\emph{Exact-Set} & Overhead & \\emph{Full-Closure} & \\emph{Eager-Pairs} \\\\",
        " & (ms) & (ms) & & (ms) & (ms, overhead) \\\\",
        "\\midrule",
    ]
    for k in sorted(exact):
        e = exact[k]
        g = eager[k]
        ablation.append(
            f"{k} & {float(e['reference_median_ns']) / 1e6:.2f} & "
            f"{float(e['candidate_median_ns']) / 1e6:.2f} & "
            f"{float(e['candidate_over_reference_time']):.2f}$\\times$ & "
            f"{float(g['reference_median_ns']) / 1e6:.2f} & "
            f"{float(g['candidate_median_ns']) / 1e6:.2f} "
            f"({float(g['candidate_over_reference_time']):.2f}$\\times$) \\\\"
        )
    ablation += ["\\bottomrule", "\\end{tabular}"]
    return ("\n".join(macros) + "\n", "\n".join(lines) + "\n",
            "\n".join(ablation) + "\n")


def generate_exit_distribution_macros(rows: List[Dict[str, Any]]) -> str:
    """Macros describing where Algorithm 2 leaves each real binary decision.

    Produced by dod_exit_distribution.py.  Every decision leaving at the first
    test is the structural reason the order relation is empty on real CFGs.
    """
    reasons = ["single_entry", "shared_entry", "decision_entry", "no_cycle",
               "transitions", "biclique"]
    decisions = sum(int(r["decisions"]) for r in rows)
    counted = {reason: sum(int(r[reason]) for r in rows) for reason in reasons}
    single_share = 100.0 * counted["single_entry"] / decisions if decisions else 0.0
    macros = [
        f"\\newcommand{{\\BinaryDecisionCount}}{{{decisions:,}}}",
        f"\\newcommand{{\\SingleEntryExitPercent}}{{{single_share:.1f}}}",
        f"\\newcommand{{\\SingleEntryExitCount}}{{{counted['single_entry']:,}}}",
        f"\\newcommand{{\\LaterExitCount}}{{{decisions - counted['single_entry']:,}}}",
    ]
    return "\n".join(macros) + "\n"


def generate_output_sensitivity_artifacts(raw_rows: List[Dict[str, Any]]) -> tuple[str, str]:
    """Macros and a table separating enumeration cost from construction cost.

    Theorem 4.23 splits the enumeration bound into O(n(n+m)) construction plus
    O(K) output.  On the closure family K grows cubically while the biclique
    incidence count C grows quadratically, so the per-triple and per-incidence
    costs show the two terms separately.
    """
    import re
    import statistics

    by_k: Dict[int, List[Dict[str, Any]]] = {}
    for row in raw_rows:
        if row.get("algorithm") != "dod-compact" or row.get("experiment") != "rq1-enumeration":
            continue
        match = re.search(r"k(\d+)", str(row["benchmark"]))
        if match:
            by_k.setdefault(int(match.group(1)), []).append(row)

    lines = [
        "% Auto-generated output-sensitivity table",
        "\\begin{tabular}{@{}rrrrr@{}}",
        "\\toprule",
        "$k$ & $K$ & $C$ & Enumeration (ns/triple) & Construction (ns/incidence) \\\\",
        "\\midrule",
    ]
    per_pair: List[float] = []
    per_incidence: List[float] = []
    for k in sorted(by_k):
        rows = by_k[k]
        pairs = int(rows[0]["dod_pairs"])
        incidences = int(rows[0]["incidences"])
        visit = statistics.median(int(r["pair_visit_ns"]) for r in rows)
        build = statistics.median(int(r["dod_ns"]) for r in rows)
        per_pair.append(visit / pairs)
        per_incidence.append(build / incidences)
        lines.append(
            f"{k} & {pairs:,} & {incidences:,} & {visit / pairs:.1f} & {build / incidences:.0f} \\\\"
        )
    lines += ["\\bottomrule", "\\end{tabular}"]

    macros = [
        "% Auto-generated output-sensitivity macros by generate_paper_artifacts.py",
        f"\\newcommand{{\\EnumNsPerTripleMin}}{{{min(per_pair):.1f}}}",
        f"\\newcommand{{\\EnumNsPerTripleMax}}{{{max(per_pair):.1f}}}",
        f"\\newcommand{{\\BuildNsPerIncidenceMin}}{{{min(per_incidence):.0f}}}",
        f"\\newcommand{{\\BuildNsPerIncidenceMax}}{{{max(per_incidence):.0f}}}",
        f"\\newcommand{{\\OutputSensitivityTripleGrowth}}{{"
        f"{int(round(max(int(r['dod_pairs']) for rows in by_k.values() for r in rows) / min(int(r['dod_pairs']) for rows in by_k.values() for r in rows)))}}}",
    ]
    return "\n".join(macros) + "\n", "\n".join(lines) + "\n"


def generate_seed_sweep_artifacts(
    summary: List[Dict[str, Any]], raw: List[Dict[str, Any]],
    family_rows: Sequence[Dict[str, Any]] = (), fixed_seed_count: int = 4,
) -> tuple[str, str]:
    """Macros and a table for the closure seed-set sensitivity sweep.

    Reporting one seeding is fragile: an even spread can align with a
    benchmark's block layout and land near the best case.  This summarises the
    distribution over randomly drawn seed sets instead.
    """
    import statistics

    sizes = sorted({int(r["seed_count"]) for r in summary})
    non_degenerate = [r for r in raw if int(r["seed_count"]) >= 4]
    speedups = sorted(float(r["speedup"]) for r in non_degenerate)
    median = statistics.median(speedups)
    q1 = speedups[len(speedups) // 4]
    q3 = speedups[3 * len(speedups) // 4]

    macros = [
        "% Auto-generated closure seed-sweep macros by generate_paper_artifacts.py",
        f"\\newcommand{{\\ClosureSweepTrials}}{{{len(non_degenerate)}}}",
        f"\\newcommand{{\\ClosureSweepMedian}}{{{median:.0f}}}",
        f"\\newcommand{{\\ClosureSweepQOne}}{{{q1:.0f}}}",
        f"\\newcommand{{\\ClosureSweepQThree}}{{{q3:.0f}}}",
        f"\\newcommand{{\\ClosureSweepMin}}{{{min(speedups):.1f}}}",
        f"\\newcommand{{\\ClosureSweepMax}}{{{max(speedups):.0f}}}",
        f"\\newcommand{{\\ClosureSweepSizeMin}}{{{min(sizes)}}}",
        f"\\newcommand{{\\ClosureSweepSizeMax}}{{{max(sizes)}}}",
        f"\\newcommand{{\\ClosureSweepDrawsPerCell}}{{"
        f"{max(int(r['trials']) for r in summary)}}}",
    ]
    # Compare each instance's fixed seeding (the closure-family table) with the
    # random draws of the same size, so "favourable end" is checked per instance
    # rather than by setting a geometric mean against a pooled median.
    fixed = [r for r in family_rows if r.get("experiment") == "rq1-closure"]
    if fixed:
        at_or_above = 0
        for r in fixed:
            draws = [float(d["speedup"]) for d in raw
                     if d["benchmark"] == r["benchmark"] and int(d["seed_count"]) == fixed_seed_count]
            if draws and float(r["reference_over_candidate_time"]) >= statistics.median(draws):
                at_or_above += 1
        macros.append(f"\\newcommand{{\\ClosureFixedAtOrAboveMedianCount}}{{{at_or_above}}}")

    lines = [
        "% Auto-generated closure seed-sweep table",
        "\\begin{tabular}{@{}rrrr@{}}",
        "\\toprule",
        "$|W|$ & Median speedup & Range & Median $|W'|$ \\\\",
        "\\midrule",
    ]
    for size in sizes:
        cells = [r for r in summary if int(r["seed_count"]) == size]
        med = statistics.median(float(r["speedup_median"]) for r in cells)
        lo = min(float(r["speedup_min"]) for r in cells)
        hi = max(float(r["speedup_max"]) for r in cells)
        closure = statistics.median(float(r["closure_median"]) for r in cells)
        lines.append(
            f"{size} & {med:.0f}$\\times$ & "
            f"{lo:.1f}--{hi:.0f}$\\times$ & {closure:.0f} \\\\"
        )
    lines += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(macros) + "\n", "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=LOTUS_ROOT / "control-dependence-results",
        help="Path to control-dependence-results directory",
    )
    parser.add_argument(
        "--closure-results-dir",
        type=Path,
        default=LOTUS_ROOT / "control-dependence-results-closure",
        help="Path to the non-trivial-closure family results directory",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=WORKSPACE_ROOT / "paper-control-dep" / "sections" / "generated",
        help="Path to output directory for generated LaTeX snippets",
    )
    args = parser.parse_args()

    summary_file = args.results_dir / "summary.csv"
    if not summary_file.is_file():
        print(f"Error: {summary_file} not found.", file=sys.stderr)
        sys.exit(1)

    summary_rows = read_summary_csv(summary_file)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    macros_code = generate_paper_macros(summary_rows)
    # Appended to paper_macros.tex rather than written as a separate file, so
    # the paper needs no extra \input for them.
    exit_stats_file = args.results_dir / "dod_exit_stats.csv"
    if exit_stats_file.is_file():
        macros_code += generate_exit_distribution_macros(read_summary_csv(exit_stats_file))
    (args.output_dir / "paper_macros.tex").write_text(macros_code)
    print(f"Written: {args.output_dir / 'paper_macros.tex'}")

    table_code = generate_subjects_table(summary_rows)
    (args.output_dir / "tab_subjects.tex").write_text(table_code)
    print(f"Written: {args.output_dir / 'tab_subjects.tex'}")

    fig3_tikz, fig4_tikz = generate_figures_tikz(summary_rows)
    (args.output_dir / "fig_rq1_results.tikz").write_text(fig3_tikz)
    print(f"Written: {args.output_dir / 'fig_rq1_results.tikz'}")
    (args.output_dir / "fig_rq1_closure.tikz").write_text(fig4_tikz)
    print(f"Written: {args.output_dir / 'fig_rq1_closure.tikz'}")

    closure_summary = args.closure_results_dir / "summary.csv"
    if closure_summary.is_file():
        closure_rows = read_summary_csv(closure_summary)
        if closure_rows:
            cf_macros, cf_table, cf_ablation = generate_closure_family_artifacts(closure_rows)
            (args.output_dir / "closure_family_macros.tex").write_text(cf_macros)
            print(f"Written: {args.output_dir / 'closure_family_macros.tex'}")
            (args.output_dir / "tab_closure_family.tex").write_text(cf_table)
            print(f"Written: {args.output_dir / 'tab_closure_family.tex'}")
            (args.output_dir / "tab_ablation_family.tex").write_text(cf_ablation)
            print(f"Written: {args.output_dir / 'tab_ablation_family.tex'}")
    else:
        print(f"Note: {closure_summary} not found; skipping closure-family artifacts")

    sweep_summary = args.closure_results_dir / "closure_seed_sweep.csv"
    sweep_raw = args.closure_results_dir / "closure_seed_sweep_raw.csv"
    if sweep_summary.is_file() and sweep_raw.is_file():
        family_rows = read_summary_csv(closure_summary) if closure_summary.is_file() else []
        closure_metadata = args.closure_results_dir / "metadata.json"
        fixed_seed_count = (
            int(json.loads(closure_metadata.read_text()).get("seed_count", 4))
            if closure_metadata.is_file() else 4
        )
        sw_macros, sw_table = generate_seed_sweep_artifacts(
            read_summary_csv(sweep_summary), read_summary_csv(sweep_raw),
            family_rows, fixed_seed_count,
        )
        (args.output_dir / "closure_sweep_macros.tex").write_text(sw_macros)
        print(f"Written: {args.output_dir / 'closure_sweep_macros.tex'}")
        (args.output_dir / "tab_closure_sweep.tex").write_text(sw_table)
        print(f"Written: {args.output_dir / 'tab_closure_sweep.tex'}")
    else:
        print(f"Note: {sweep_summary} not found; skipping seed-sweep artifacts")

    closure_raw = args.closure_results_dir / "raw.csv"
    if closure_raw.is_file():
        os_macros, os_table = generate_output_sensitivity_artifacts(read_summary_csv(closure_raw))
        (args.output_dir / "output_sensitivity_macros.tex").write_text(os_macros)
        print(f"Written: {args.output_dir / 'output_sensitivity_macros.tex'}")
        (args.output_dir / "tab_output_sensitivity.tex").write_text(os_table)
        print(f"Written: {args.output_dir / 'tab_output_sensitivity.tex'}")
    else:
        print(f"Note: {closure_raw} not found; skipping output-sensitivity artifacts")



if __name__ == "__main__":
    main()
