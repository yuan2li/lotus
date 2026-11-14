#!/usr/bin/env python3
"""Summarize evaluate_usetracessa.py results into paper tables and plot data.

Repeats of one configuration are reduced to medians. A configuration whose
runs did not all succeed is labeled instead of averaged: TO (timeout), OOM,
INC (search budget exhausted), PE (unparsed output), SPEC or ERR. Unknown results
without a complete model are labeled UNK in paper cells; their raw metrics and
`unknown_incomplete_model` remain in runs.csv. Missing configurations are NA.

Main comparisons default to abstract objects for UAF and site objects for the
other clients (D5), MSSA auto and Saber nosmt. --temporal-graph selects the
main UseTraceSSA skeleton without changing tool defaults or deciding P1.
Table 5 covers {sparse, svfg} x {batch, fixed}, separately for each call-passing
setting present in the input. All selections are recorded in selection.json.

Outputs (in --out-dir):
    runs.csv          every configuration with medians and run counts
    table2.csv/.tex   RQ1: per program and client, Saber / UFG / UseTraceSSA
    table4.csv/.tex   RQ2: CFG nodes, UseTraceSSA nodes, D_temporal, k distribution
                      (median, P90, max, mean), sigma, D_alias
    table5.csv/.tex   RQ3: fixed vs batch for each skeleton/call-passing setting
    rq1_time.csv      Figure 5: analysis seconds per program, client and tool
    pta_ablation.csv  Andersen vs flow-sensitive points-to (--ptas): universe, k,
                      sigma, findings and time for UseTraceSSA and UFG
    object_scope.csv  heap-only vs every points-to object (--object-scopes): the
                      same columns plus the excluded non-heap objects
    push_sizes.csv    |New_i| histograms with skeleton/mode/call-passing dimensions
    guard_k.csv       alias multiplicity k histograms with the same dimensions
    selection.json   main-table configuration and status legend

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
               "temporal_graph", "object_granularity", "svfg_mssa", "pta", "object_scope",
               "pass_calls", "context_mode", "context_limit")
METRICS = ("analysis_ms", "total_ms", "check_ms", "build_ms", "parse_ms", "svfg_ms", "pta_ms",
           "max_rss_mb", "wall_time_s", "findings", "finding_sites",
           "product_states", "product_edges", "summary_pairs", "pushes",
           "pushed_object_bits", "sigma", "candidate_objects", "found_objects",
           "history_nodes", "history_psi", "history_phi", "ufg_nodes",
           "excluded_nonheap_objects", "guard_k_count", "guard_k_mean", "guard_k_median", "guard_k_p90", "guard_k_max",
           "icfg_nodes", "ir_instructions", "ir_blocks", "ir_functions",
           # Temporal graph size (every graph) and the event-order searches of
           # the SVFG-based baseline (--temporal-graph=svfg).
           "flow_nodes", "flow_edges", "ordering_searches", "ordering_block_visits",
           "ordering_edges", "ordering_edge_objects", "usetracessa_event_order_ms",
           # Path feasibility of the reported witnesses (--path-feasibility, RQ4).
           "feasible_sites", "infeasible_sites", "feasibility_unknown_sites",
           "feasibility_witnesses", "infeasible_witnesses", "feasibility_ms")
HISTOGRAMS = ("push_sizes", "guard_k_hist")
FRACTIONAL = ("sigma", "guard_k_mean", "max_rss_mb")
LABELS = {"TIMEOUT": "TO", "OOM": "OOM", "INCOMPLETE": "INC", "PARSE_ERROR": "PE",
          "SPEC_ERROR": "SPEC"}
CLIENTS = {"double-free": "DF", "use-after-free": "UAF",
           "memory-leak": "Leak (heap)", "file-leak": "Leak (file)"}


def load(paths):
    records = []
    for path in paths:
        data = json.loads(Path(path).read_text())
        # Older result files are a bare list of records.
        records += data["records"] if isinstance(data, dict) else data
    for record in records:
        if not record.get("benchmark"):
            record["benchmark"] = Path(record["bc"]).stem
        record.setdefault("repeat", 0)
    return records


def label(status):
    return LABELS.get(status, "ERR")


def aggregate(records):
    groups = {}
    for record in records:
        defaults = {"smt_mode": "nosmt", "context_mode": "sensitive", "context_limit": 3}
        if record["tool"] != "saber":
            defaults.update(object_granularity="site", svfg_mssa="auto", pta="andersen",
                            object_scope="heap", pass_calls=True)
        if record["tool"] == "usetracessa":
            defaults.update(object_mode="batch", temporal_graph="sparse")
        # Missing historical fields have known defaults. An explicit None
        # context limit still means unbounded and must not merge with depth 3.
        configuration = {name: record.get(name) for name in CONFIG_KEYS}
        for name, value in defaults.items():
            if name not in record or (record[name] is None and name != "context_limit"):
                configuration[name] = value
        key = tuple(configuration[name] for name in CONFIG_KEYS)
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
        row["unknown_incomplete_model"] = any(
            run.get("unknown_incomplete_model") or
            (run.get("check_result") == "Unknown" and run.get("model_complete") is False)
            for run in runs)
        if not failed and row["unknown_incomplete_model"]:
            row["status"] = "UNK"
        # Runs retried after a spec file could not be read (records from
        # before the retry have no such field).
        row["max_attempts"] = max((run.get("attempts", 1) for run in runs), default=1)
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
    defaults = {"smt_mode": "nosmt", "context_mode": "sensitive", "context_limit": 3}
    if tool != "saber":
        defaults.update(object_granularity="abstract" if bug_type == "use-after-free" else "site",
                        svfg_mssa="auto", pta="andersen", object_scope="heap", pass_calls=True)
    if tool == "usetracessa":
        defaults.update(object_mode="batch", temporal_graph="sparse")
    wanted = dict(defaults, **config)
    for row in rows.values():
        if (row["benchmark"] == benchmark and row["bug_type"] == bug_type and
                row["tool"] == tool and
                all(row.get(name) == value for name, value in wanted.items())):
            return row
    return None


def cell(row, metric, scale=1.0, digits=2):
    if row is None:
        return "NA"
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
    if numerator is None or denominator is None or denominator == 0:
        return "NA"
    return f"{numerator / denominator:.{digits}f}"


def status(row):
    return row["status"] if row else "NA"


def ratio_cells(numerator, numerator_metric, denominator, denominator_metric):
    for row in (numerator, denominator):
        if status(row) not in ("OK", "NA"):
            return status(row)
    if numerator is None or denominator is None:
        return "NA"
    return ratio(numerator.get(numerator_metric), denominator.get(denominator_metric))


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", type=Path, nargs="+")
    parser.add_argument("--out-dir", type=Path, default=Path("summary"))
    parser.add_argument("--temporal-graph", choices=("sparse", "svfg", "icfg"), default="sparse",
                        help="Skeleton for Tables 2/4 and rq1_time.csv; Table 5 always covers both")
    parser.add_argument("--object-granularity", choices=("auto", "site", "abstract"), default="auto",
                        help="auto: abstract for UAF, site for DF/leaks (D5)")
    parser.add_argument("--pass-calls", choices=("true", "false"), default="true")
    parser.add_argument("--svfg-mssa", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--pta", choices=("andersen", "fspta"), default="andersen")
    parser.add_argument("--object-scope", choices=("heap", "all"), default="heap")
    parser.add_argument("--context", choices=("sensitive", "insensitive"), default="sensitive")
    parser.add_argument("--context-limit", default="3", help="Call-string depth, or unlimited")
    args = parser.parse_args(argv)
    try:
        limit = None if args.context_limit == "unlimited" else int(args.context_limit)
        if limit is not None and limit < 0:
            raise ValueError
    except ValueError:
        parser.error("--context-limit must be a nonnegative integer or unlimited")
    rows = aggregate(load(args.results))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    selection = {"temporal_graph": args.temporal_graph,
                 "object_granularity": args.object_granularity,
                 "auto_granularity": {bug: "abstract" if bug == "use-after-free" else "site"
                                      for bug in CLIENTS},
                 "pass_calls": args.pass_calls == "true", "svfg_mssa": args.svfg_mssa,
                 "pta": args.pta, "object_scope": args.object_scope,
                 "context_mode": args.context, "context_limit": limit,
                 "status_legend": {"OK": "successful run", "UNK": "unknown, incomplete model",
                                   "NA": "configuration absent", "TO": "timeout", "OOM": "out of memory",
                                   "INC": "search incomplete", "PE": "parse error",
                                   "SPEC": "spec load error", "ERR": "other error"}}
    (args.out_dir / "selection.json").write_text(json.dumps(selection, indent=2) + "\n")

    header = list(CONFIG_KEYS) + ["kloc", "debug_lines", "runs", "status", "check_result",
                                  "unknown_incomplete_model", "max_attempts"] + list(METRICS) + list(HISTOGRAMS)
    write_csv(args.out_dir / "runs.csv", header,
              [[row.get(name) for name in header] for row in
               sorted(rows.values(), key=lambda r: tuple(str(r.get(k)) for k in CONFIG_KEYS))])

    pairs = sorted({(row["benchmark"], row["bug_type"]) for row in rows.values()},
                   key=lambda pair: (pair[0], list(CLIENTS).index(pair[1])
                                     if pair[1] in CLIENTS else 99))
    table2, table4, table5, times, pushes, guards, ablation, scopes = ([] for _ in range(8))
    for benchmark, bug_type in pairs:
        client = CLIENTS.get(bug_type, bug_type)
        granularity = (selection["auto_granularity"].get(bug_type, "site")
                       if args.object_granularity == "auto" else args.object_granularity)

        def select(tool, **overrides):
            config = {"context_mode": args.context, "context_limit": limit}
            if tool != "saber":
                config.update(object_granularity=granularity, svfg_mssa=args.svfg_mssa,
                              pta=args.pta, object_scope=args.object_scope,
                              pass_calls=selection["pass_calls"])
            if tool == "usetracessa":
                config["temporal_graph"] = args.temporal_graph
            config.update(overrides)
            return find(rows, benchmark, bug_type, tool, **config)

        saber = select("saber")
        ufg = select("ufg")
        utssa = select("usetracessa")
        fixed = select("usetracessa", object_mode="fixed")
        icfg = select("usetracessa", temporal_graph="icfg")
        dimensions = [args.temporal_graph, granularity, selection["pass_calls"]]
        any_row = utssa or ufg or saber
        kloc = any_row.get("kloc") if any_row else None
        table2.append([benchmark, client, kloc or "",
                       cell(utssa, "guard_k_mean"),
                       cell(saber, "max_rss_mb", digits=1), cell(saber, "analysis_ms", 1000),
                       cell(ufg, "product_states"), cell(ufg, "max_rss_mb", digits=1),
                       cell(ufg, "analysis_ms", 1000),
                       cell(utssa, "product_states"), cell(utssa, "max_rss_mb", digits=1),
                       cell(utssa, "analysis_ms", 1000)] + dimensions +
                      [status(saber), status(ufg), status(utssa)])
        if utssa:
            size_metric = "history_nodes" if args.temporal_graph == "sparse" else "flow_nodes"
            table4.append([benchmark, client, cell(utssa, "ir_instructions"),
                           cell(utssa, size_metric),
                           ratio_cells(utssa, "ir_instructions", utssa, size_metric),
                           cell(utssa, "guard_k_median"), cell(utssa, "guard_k_p90"),
                           cell(utssa, "guard_k_max"), cell(utssa, "guard_k_mean"),
                           cell(utssa, "sigma"),
                           ratio_cells(ufg, "product_states", utssa, "product_states")] +
                          dimensions + [status(utssa)])
        for graph in ("sparse", "svfg"):
            for passing in (True, False):
                batch = select("usetracessa", temporal_graph=graph, pass_calls=passing)
                single = select("usetracessa", temporal_graph=graph, pass_calls=passing,
                                object_mode="fixed")
                dense = select("usetracessa", temporal_graph="icfg", pass_calls=passing)
                if batch is None and single is None:
                    continue
                table5.append([benchmark, client, graph, granularity, passing,
                               cell(batch or single, "candidate_objects"),
                               cell(single, "pushes"), cell(single, "pushed_object_bits"),
                               cell(single, "summary_pairs"), cell(single, "analysis_ms", 1000, 3),
                               cell(single, "sigma"), cell(batch, "pushes"),
                               cell(batch, "pushed_object_bits"), cell(batch, "summary_pairs"),
                               cell(batch, "analysis_ms", 1000, 3), cell(batch, "sigma"),
                               cell(dense, "analysis_ms", 1000, 3),
                               status(single), status(batch), status(dense)])
                for mode, row in (("batch", batch), ("fixed", single)):
                    if status(row) != "OK":
                        continue
                    config = [graph, mode, granularity, passing]
                    if row.get("push_sizes"):
                        pushes += [[benchmark, client, 2 ** bucket, count] + config
                                   for bucket, count in enumerate(row["push_sizes"])]
                    if row.get("guard_k_hist"):
                        guards += [[benchmark, client, 2 ** bucket, count] + config
                                   for bucket, count in enumerate(row["guard_k_hist"])]
        times.append([benchmark, client, cell(saber, "analysis_ms", 1000, 3),
                      cell(ufg, "analysis_ms", 1000, 3), cell(utssa, "analysis_ms", 1000, 3),
                      cell(fixed, "analysis_ms", 1000, 3), cell(icfg, "analysis_ms", 1000, 3)] +
                     dimensions + [status(saber), status(ufg), status(utssa), status(fixed), status(icfg)])
        for tool in ("usetracessa", "ufg"):
            tool_dimensions = [args.temporal_graph if tool == "usetracessa" else "",
                               granularity, selection["pass_calls"]]
            fspta = select(tool, pta="fspta")
            if fspta is None:
                continue
            base = select(tool, pta="andersen")
            for pta, row in (("andersen", base), ("fspta", fspta)):
                ablation.append([benchmark, client, tool, pta,
                                 cell(row, "candidate_objects"), cell(row, "guard_k_mean"),
                                 cell(row, "guard_k_p90"), cell(row, "guard_k_max"),
                                 cell(row, "sigma"), cell(row, "findings"),
                                 cell(row, "finding_sites"), cell(row, "product_states"),
                                 cell(row, "pta_ms", 1000, 3), cell(row, "analysis_ms", 1000, 3)] +
                                tool_dimensions + [status(row)])
        for tool in ("usetracessa", "ufg"):
            tool_dimensions = [args.temporal_graph if tool == "usetracessa" else "",
                               granularity, selection["pass_calls"]]
            every = select(tool, object_scope="all")
            if every is None:
                continue
            heap = select(tool, object_scope="heap")
            for scope, row in (("heap", heap), ("all", every)):
                scopes.append([benchmark, client, tool, scope,
                               cell(row, "candidate_objects"),
                               cell(row, "excluded_nonheap_objects"),
                               cell(row, "guard_k_mean"), cell(row, "guard_k_p90"),
                               cell(row, "guard_k_max"), cell(row, "sigma"),
                               cell(row, "findings"), cell(row, "finding_sites"),
                               cell(row, "product_states"), cell(row, "analysis_ms", 1000, 3)] +
                              tool_dimensions + [status(row)])

    out = args.out_dir
    head2 = ["program", "client", "kloc", "alias_k", "saber_mem_mb", "saber_time_s",
             "ufg_states", "ufg_mem_mb", "ufg_time_s", "utssa_states", "utssa_mem_mb",
             "utssa_time_s", "temporal_graph", "object_granularity", "pass_calls",
             "saber_status", "ufg_status", "utssa_status"]
    write_csv(out / "table2.csv", head2, table2)
    write_tex(out / "table2.tex", table2)
    head4 = ["program", "client", "cfg_nodes", "utssa_nodes", "d_temporal", "median_k",
             "p90_k", "max_k", "mean_k", "sigma", "d_alias_states",
             "temporal_graph", "object_granularity", "pass_calls", "status"]
    write_csv(out / "table4.csv", head4, table4)
    write_tex(out / "table4.tex", table4)
    head5 = ["program", "client", "temporal_graph", "object_granularity", "pass_calls", "universe",
             "fixed_pushes", "fixed_pushed_bits", "fixed_summaries", "fixed_time_s", "fixed_sigma",
             "batch_pushes", "batch_pushed_bits", "batch_summaries", "batch_time_s", "sigma",
             "ide_icfg_time_s", "fixed_status", "batch_status", "icfg_status"]
    write_csv(out / "table5.csv", head5, table5)
    write_tex(out / "table5.tex", table5)
    write_csv(out / "rq1_time.csv", ["program", "client", "saber", "ufg", "utssa",
                                     "utssa_fixed", "ide_icfg", "temporal_graph", "object_granularity",
                                     "pass_calls", "saber_status", "ufg_status", "utssa_status",
                                     "fixed_status", "icfg_status"], times)
    write_csv(out / "pta_ablation.csv",
              ["program", "client", "tool", "pta", "universe", "mean_k", "p90_k", "max_k",
               "sigma", "findings", "finding_sites", "product_states", "pta_time_s",
               "analysis_time_s", "temporal_graph", "object_granularity", "pass_calls", "status"], ablation)
    write_csv(out / "object_scope.csv",
              ["program", "client", "tool", "scope", "universe", "excluded_nonheap",
               "mean_k", "p90_k", "max_k", "sigma", "findings", "finding_sites",
               "product_states", "analysis_time_s", "temporal_graph", "object_granularity",
               "pass_calls", "status"], scopes)
    hist_config = ["temporal_graph", "object_mode", "object_granularity", "pass_calls"]
    write_csv(out / "push_sizes.csv", ["program", "client", "bucket_min", "pushes"] + hist_config, pushes)
    write_csv(out / "guard_k.csv", ["program", "client", "bucket_min", "sites"] + hist_config, guards)
    print(f"{len(rows)} configurations, {len(pairs)} program/client pairs -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
