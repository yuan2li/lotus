#!/usr/bin/env python3
"""Summarize evaluate_usetracessa.py results into paper tables and plot data.

Repeats of one configuration are reduced to medians. A configuration whose
runs did not all succeed is labeled instead of averaged: TO (timeout), OOM,
INC (search budget exhausted), PE (unparsed output) or ERR. Unknown results
without a complete model are marked with `unknown_incomplete_model` in runs.csv.

Main comparisons use the site object granularity, SVFG Memory SSA `auto`,
Saber without SMT, and UseTraceSSA batch search on the sparse graph
(decisions 1-4); the fixed-object and IDE-ICFG configurations feed Table 5.

Outputs (in --out-dir):
    runs.csv          every configuration with medians and run counts
    table2.csv/.tex   RQ1: per program and client, Saber / UFG / UseTraceSSA
    table4.csv/.tex   RQ2: CFG nodes, UseTraceSSA nodes, D_temporal, k distribution
                      (median, P90, max, mean), sigma, D_alias
    table5.csv/.tex   RQ3: fixed vs batch pushes, summaries, time, sigma, IDE-ICFG time
    rq1_time.csv      Figure 5: analysis seconds per program, client and tool
    push_sizes.csv    |New_i| histogram per program and client (power-of-two buckets)
    guard_k.csv       alias multiplicity k histogram per program and client

Example:
    python3 scripts/summarize_usetracessa.py eval_results.json \\
        --out-dir ../paper-utssa/data
"""
import argparse
import csv
import json
from pathlib import Path
import statistics
import sys

CONFIG_KEYS = ("benchmark", "tool", "bug_type", "smt_mode", "object_mode",
               "temporal_graph", "object_granularity", "svfg_mssa")
METRICS = ("analysis_ms", "total_ms", "check_ms", "build_ms", "parse_ms", "svfg_ms",
           "max_rss_mb", "wall_time_s", "findings", "finding_sites",
           "product_states", "product_edges", "summary_pairs", "pushes",
           "pushed_object_bits", "sigma", "candidate_objects", "found_objects",
           "history_nodes", "history_psi", "history_phi", "ufg_nodes",
           "guard_k_count", "guard_k_mean", "guard_k_median", "guard_k_p90", "guard_k_max",
           "icfg_nodes", "ir_instructions", "ir_blocks", "ir_functions")
HISTOGRAMS = ("push_sizes", "guard_k_hist")
FRACTIONAL = ("sigma", "guard_k_mean", "max_rss_mb")
LABELS = {"TIMEOUT": "TO", "OOM": "OOM", "INCOMPLETE": "INC", "PARSE_ERROR": "PE"}
CLIENTS = {"double-free": "DF", "use-after-free": "UAF",
           "memory-leak": "Leak (heap)", "file-leak": "Leak (file)"}


def load(paths):
    records = []
    for path in paths:
        data = json.loads(Path(path).read_text())
        # Older result files are a bare list of records.
        records += data["records"] if isinstance(data, dict) else data
    for record in records:
        record.setdefault("benchmark", Path(record["bc"]).stem)
        record.setdefault("repeat", 0)
    return records


def label(status):
    return LABELS.get(status, "ERR")


def aggregate(records):
    groups = {}
    for record in records:
        key = tuple(record.get(name) for name in CONFIG_KEYS)
        groups.setdefault(key, []).append(record)
    rows = {}
    for key, runs in groups.items():
        row = dict(zip(CONFIG_KEYS, key))
        row["runs"] = len(runs)
        row["kloc"] = runs[0].get("kloc")
        row["debug_lines"] = runs[0].get("debug_lines")
        failed = [run["status"] for run in runs if run["status"] != "SUCCESS"]
        row["status"] = label(failed[0]) if failed else "OK"
        row["check_result"] = runs[0].get("check_result")
        row["unknown_incomplete_model"] = any(run.get("unknown_incomplete_model") for run in runs)
        ok = [run for run in runs if run["status"] == "SUCCESS"]
        for metric in METRICS:
            values = [run[metric] for run in ok
                      if isinstance(run.get(metric), (int, float))]
            row[metric] = statistics.median(values) if values and not failed else None
        for histogram in HISTOGRAMS:
            row[histogram] = ok[0].get(histogram) if ok else None
        rows[key] = row
    return rows


def find(rows, benchmark, bug_type, tool, **config):
    defaults = {"usetracessa": {"smt_mode": "nosmt", "object_mode": "batch",
                                "temporal_graph": "sparse"},
                "ufg": {"smt_mode": "nosmt"},
                "saber": {"smt_mode": "nosmt"}}[tool]
    wanted = dict(defaults, **config)
    for row in rows.values():
        if (row["benchmark"] == benchmark and row["bug_type"] == bug_type and
                row["tool"] == tool and
                all(row.get(name) == value for name, value in wanted.items())):
            if tool != "saber" and row["object_granularity"] not in (
                    None, config.get("object_granularity", "site")):
                continue
            if tool != "saber" and row["svfg_mssa"] not in (None, config.get("svfg_mssa", "auto")):
                continue
            return row
    return None


def cell(row, metric, scale=1.0, digits=2):
    if row is None:
        return ""
    if row["status"] != "OK":
        return row["status"]
    value = row.get(metric)
    if value is None:
        return ""
    value = value / scale
    # Medians of an even number of repeats are floats even for counts.
    if scale == 1.0 and metric not in FRACTIONAL and float(value).is_integer():
        return str(int(value))
    return f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def ratio(numerator, denominator, digits=1):
    if not numerator or not denominator:
        return ""
    return f"{numerator / denominator:.{digits}f}"


def write_csv(path, header, rows):
    with path.open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(header)
        writer.writerows(rows)


def write_tex(path, rows):
    with path.open("w") as output:
        for row in rows:
            output.write(" & ".join(str(value).replace("_", r"\_") for value in row) +
                         r" \\" + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", type=Path, nargs="+")
    parser.add_argument("--out-dir", type=Path, default=Path("summary"))
    args = parser.parse_args()
    rows = aggregate(load(args.results))
    args.out_dir.mkdir(parents=True, exist_ok=True)

    header = list(CONFIG_KEYS) + ["kloc", "debug_lines", "runs", "status", "check_result",
                                  "unknown_incomplete_model"] + list(METRICS) + list(HISTOGRAMS)
    write_csv(args.out_dir / "runs.csv", header,
              [[row.get(name) for name in header] for row in
               sorted(rows.values(), key=lambda r: tuple(str(r.get(k)) for k in CONFIG_KEYS))])

    pairs = sorted({(row["benchmark"], row["bug_type"]) for row in rows.values()},
                   key=lambda pair: (pair[0], list(CLIENTS).index(pair[1])
                                     if pair[1] in CLIENTS else 99))
    table2, table4, table5, times, pushes, guards = [], [], [], [], [], []
    for benchmark, bug_type in pairs:
        client = CLIENTS.get(bug_type, bug_type)
        saber = find(rows, benchmark, bug_type, "saber")
        ufg = find(rows, benchmark, bug_type, "ufg")
        utssa = find(rows, benchmark, bug_type, "usetracessa")
        fixed = find(rows, benchmark, bug_type, "usetracessa", object_mode="fixed")
        icfg = find(rows, benchmark, bug_type, "usetracessa", temporal_graph="icfg")
        any_row = utssa or ufg or saber
        kloc = any_row.get("kloc") if any_row else None
        table2.append([benchmark, client, kloc or "",
                       cell(utssa, "guard_k_mean"),
                       cell(saber, "max_rss_mb", digits=1), cell(saber, "analysis_ms", 1000),
                       cell(ufg, "product_states"), cell(ufg, "max_rss_mb", digits=1),
                       cell(ufg, "analysis_ms", 1000),
                       cell(utssa, "product_states"), cell(utssa, "max_rss_mb", digits=1),
                       cell(utssa, "analysis_ms", 1000)])
        if utssa and utssa["status"] == "OK":
            table4.append([benchmark, client, cell(utssa, "ir_instructions"),
                           cell(utssa, "history_nodes"),
                           ratio(utssa.get("ir_instructions"), utssa.get("history_nodes")),
                           cell(utssa, "guard_k_median"), cell(utssa, "guard_k_p90"),
                           cell(utssa, "guard_k_max"), cell(utssa, "guard_k_mean"),
                           cell(utssa, "sigma"),
                           ratio(ufg and ufg["status"] == "OK" and ufg.get("product_states"),
                                 utssa.get("product_states"))])
        if utssa or fixed:
            table5.append([benchmark, client, cell(utssa or fixed, "candidate_objects"),
                           cell(fixed, "pushes"), cell(fixed, "summary_pairs"),
                           cell(fixed, "analysis_ms", 1000, 3),
                           cell(utssa, "pushes"), cell(utssa, "summary_pairs"),
                           cell(utssa, "analysis_ms", 1000, 3), cell(utssa, "sigma"),
                           cell(icfg, "analysis_ms", 1000, 3)])
        times.append([benchmark, client, cell(saber, "analysis_ms", 1000, 3),
                      cell(ufg, "analysis_ms", 1000, 3), cell(utssa, "analysis_ms", 1000, 3),
                      cell(fixed, "analysis_ms", 1000, 3), cell(icfg, "analysis_ms", 1000, 3)])
        if utssa and utssa.get("push_sizes"):
            pushes += [[benchmark, client, 2 ** bucket, count]
                       for bucket, count in enumerate(utssa["push_sizes"])]
        if utssa and utssa.get("guard_k_hist"):
            guards += [[benchmark, client, 2 ** bucket, count]
                       for bucket, count in enumerate(utssa["guard_k_hist"])]

    out = args.out_dir
    head2 = ["program", "client", "kloc", "alias_k", "saber_mem_mb", "saber_time_s",
             "ufg_states", "ufg_mem_mb", "ufg_time_s", "utssa_states", "utssa_mem_mb",
             "utssa_time_s"]
    write_csv(out / "table2.csv", head2, table2)
    write_tex(out / "table2.tex", table2)
    head4 = ["program", "client", "cfg_nodes", "utssa_nodes", "d_temporal", "median_k",
             "p90_k", "max_k", "mean_k", "sigma", "d_alias_states"]
    write_csv(out / "table4.csv", head4, table4)
    write_tex(out / "table4.tex", table4)
    head5 = ["program", "client", "universe", "fixed_pushes", "fixed_summaries",
             "fixed_time_s", "batch_pushes", "batch_summaries", "batch_time_s", "sigma",
             "ide_icfg_time_s"]
    write_csv(out / "table5.csv", head5, table5)
    write_tex(out / "table5.tex", table5)
    write_csv(out / "rq1_time.csv", ["program", "client", "saber", "ufg", "utssa",
                                     "utssa_fixed", "ide_icfg"], times)
    write_csv(out / "push_sizes.csv", ["program", "client", "bucket_min", "pushes"], pushes)
    write_csv(out / "guard_k.csv", ["program", "client", "bucket_min", "sites"], guards)
    print(f"{len(rows)} configurations, {len(pairs)} program/client pairs -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
