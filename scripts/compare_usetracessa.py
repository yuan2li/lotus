#!/usr/bin/env python3
"""Cross-check evaluate_usetracessa.py runs made with --details-dir.

oracle  Differential oracle (RQ4). For each program, client and object
        granularity, compare UseTraceSSA batch on the sparse graph (the
        reference) with every other UseTraceSSA configuration and with UFG:
        the status of every object and the set of (sink site, object) findings.
        Runs with different points-to sets (--ptas), object scopes
        (--object-scopes) or call passing (--pass-calls) are compared
        separately.
        Reports the number of compared queries and lists every mismatch;
        exits with 1 for mismatches, or 2 when coverage cannot be verified
        (failed/missing runs, missing references, or legacy abstract IDs
        without stable object keys). Empty object universes are valid comparisons.

match   Cross-tool alert matching and a manual sampling list. Aligns
        UseTraceSSA, UFG and Saber reports by source file and line: the sink
        for use-after-free, and the allocation for double-free and leaks,
        because Saber reports those once per allocation (all its double-free
        diagnostic steps are the allocation). UseTraceSSA/UFG allocations come
        from the exported object locations; older results without them fall
        back to the sink (double-free) or the witness source (leaks, needs
        --witnesses). Writes a CSV with one row per location and the tools
        reporting it, plus a seeded random sample. Selects one temporal graph,
        call-passing setting, MSSA setting and context; ablations are not pooled.
        Zero-alert tools retain a 0 column; failed, missing or incomplete
        negative results are NA with explicit status columns. A sidecar
        .coverage.csv includes even program/client pairs with no alerts.
        Returns 2 if coverage is incomplete. --tools declares expected tools
        (default all three); budgets and feasibility are selected explicitly.

Multiple input files must identify one verified commit and clean source trees.
Single legacy files without commit metadata remain readable.

Examples:
    python3 scripts/compare_usetracessa.py oracle eval_results.json
    python3 scripts/compare_usetracessa.py match eval_results.json \\
        --out matches.csv --sample 20
"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import random
import sys
from usetracessa_results import (STATUS_LABELS, add_analysis_options, canonical_limits,
                                feasibility_enabled, load_results, selected_limits)

REFERENCE = {"tool": "usetracessa", "object_mode": "batch", "temporal_graph": "sparse"}
LEAKS = ("memory-leak", "file-leak")
# Saber reports these once per allocation; align every tool by allocation.
BY_ALLOCATION = LEAKS + ("double-free",)


def load_records(paths):
    return load_results(paths)


def benchmark(record):
    return record.get("benchmark") or Path(record["bc"]).stem


def config_name(record):
    if record["tool"] == "usetracessa":
        return f"usetracessa/{record['object_mode']}/{record['temporal_graph']}"
    if record["tool"] == "saber":
        return f"saber/{record['smt_mode']}"
    return record["tool"]


def findings(details):
    return {(finding["site"], obj) for finding in details["findings"]
            for obj in finding["objects"]}


def keyed(details):
    """Run-independent (key, status) and (site, key) multisets, or None for
    results written before the tools exported object keys."""
    if any("key" not in o for o in details["objects"]):
        return None
    keys = {o["object"]: o["key"] for o in details["objects"]}
    return (Counter((o["key"], o["status"]) for o in details["objects"]),
            Counter((finding["site"], keys[obj]) for finding in details["findings"]
                    for obj in finding["objects"]))


def multiset_diffs(expected, actual, what, other):
    diffs = [f"{what} only in reference: {item} x{count}"
             for item, count in sorted((expected - actual).items())]
    diffs += [f"{what} only in {other}: {item} x{count}"
              for item, count in sorted((actual - expected).items())]
    return diffs


def oracle_payload(record):
    details = json.loads(Path(record["details"]).read_text())
    return ({o["object"]: o["status"] for o in details["objects"]},
            findings(details), keyed(details))


def oracle(records):
    groups = {}
    for record in records:
        if record["tool"] == "saber" or record.get("repeat", 0) != 0:
            continue
        # Runs that pass over calls and runs that enter every callee may
        # differ under a context bound; each is compared with its own sparse
        # reference. Records from before the call bypass have no such field.
        passing = record.get("pass_calls")
        key = (benchmark(record), record["bug_type"], record["object_granularity"],
               record.get("pta") or "andersen", record.get("object_scope") or "heap",
               "pass-calls" if passing in (None, True) else "enter-calls",
               record.get("svfg_mssa") or "auto",
               record.get("context_mode", "sensitive"), record.get("context_limit", 3))
        groups.setdefault(key, []).append(record)
    compared = comparisons = mismatches = unverified = 0
    for key in sorted(groups, key=str):
        runs = groups[key]
        name = "/".join(map(str, key))
        usable = []
        for run in runs:
            if run.get("status") != "SUCCESS" or not run.get("details"):
                print(f"{name}: unverified {config_name(run)}: "
                      f"{run.get('status')}, details={bool(run.get('details'))}")
                unverified += 1
            else:
                usable.append(run)
        references = [run for run in usable if all(
            run.get(field) == value for field, value in REFERENCE.items())]
        if len(references) != 1:
            print(f"{name}: expected one reference run, got {len(references)}; unverified")
            unverified += 1
            continue
        reference = references[0]
        if len(usable) == 1:
            print(f"{name}: no comparison run; unverified")
            unverified += 1
        try:
            statuses, expected_findings, expected_keys = oracle_payload(reference)
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"{name}: unreadable/invalid reference details: {error}")
            unverified += 1
            continue
        for run in usable:
            if run is reference:
                continue
            try:
                actual_statuses, actual_findings, actual_keys = oracle_payload(run)
            except (OSError, ValueError, KeyError, TypeError) as error:
                print(f"{name} vs {config_name(run)}: unreadable/invalid details: {error}")
                unverified += 1
                continue
            objects = set(statuses) | set(actual_statuses)
            queries = max(len(statuses), len(actual_statuses))
            note = ""
            if expected_keys is not None and actual_keys is not None:
                # Abstract SVFG object IDs can differ between processes; the
                # exported keys name objects independently of the run.
                note = " (compared by object keys)"
                diffs = multiset_diffs(expected_keys[0], actual_keys[0], "object",
                                       config_name(run))
                diffs += multiset_diffs(expected_keys[1], actual_keys[1], "finding",
                                        config_name(run))
            elif key[2] == "abstract":
                print(f"{name} vs {config_name(run)}: abstract objects lack stable keys; unverified")
                unverified += 1
                continue
            else:
                diffs = [f"object {obj}: {statuses.get(obj)} vs {actual_statuses.get(obj)}"
                         for obj in sorted(objects)
                         if statuses.get(obj) != actual_statuses.get(obj)]
                diffs += [f"finding only in reference: {site} object {obj}"
                          for site, obj in sorted(expected_findings - actual_findings)]
                diffs += [f"finding only in {config_name(run)}: {site} object {obj}"
                          for site, obj in sorted(actual_findings - expected_findings)]
            comparisons += 1
            compared += queries
            print(f"{name} vs {config_name(run)}: {queries} objects, "
                  f"{len(actual_findings)} findings, {len(diffs)} mismatches{note}")
            for diff in diffs:
                print(f"  {diff}")
            mismatches += len(diffs)
    print(f"compared queries (object x configuration): {compared}; "
          f"comparisons: {comparisons}; mismatches: {mismatches}; unverified: {unverified}")
    if unverified or not comparisons:
        return 2
    return 1 if mismatches else 0


def location(text):
    """Normalize 'func:path/file.c:line:col' or a file path plus line to file:line."""
    parts = text.split(":")
    if len(parts) >= 3 and parts[-1].isdigit() and parts[-2].isdigit():
        return f"{Path(parts[-3]).name}:{parts[-2]}"
    return None


def ir_locations(record, details):
    result = set()
    allocated = {o["object"]: o.get("location") for o in details["objects"]}
    for finding in details["findings"]:
        sites = [allocated.get(obj) for obj in finding["objects"]]
        if record["bug_type"] in BY_ALLOCATION and sites and all(sites):
            result.update(location("x:" + site + ":0") for site in sites)
        elif record["bug_type"] in LEAKS:
            steps = [step for step in finding.get("witness", []) if step["location"]]
            if steps:
                result.add(location("x:" + steps[0]["location"]))
        else:
            result.add(location(finding["site"]))
    result.discard(None)
    return result


def saber_locations(record, report):
    result = set()
    for bug_type in report.get("BugTypes", []):
        for bug in bug_type.get("Reports", []):
            steps = [step for step in bug.get("DiagSteps", []) if step.get("Line")]
            if not steps or not bug.get("Valid", True):
                continue
            step = steps[0] if record["bug_type"] in BY_ALLOCATION else steps[-1]
            result.add(f"{Path(step['File']).name}:{step['Line']}")
    return result


def match(records, out, sample, seed, granularity="site", pta="andersen",
          pass_calls=True, temporal_graph="sparse", svfg_mssa="auto",
          context_mode="sensitive", context_limit=3, tools=None,
          path_feasibility=False, budgets=None, saber_budgets=None):
    requested = set(tools) if tools is not None else {r["tool"] for r in records}
    groups = {}
    for record in records:
        if record["tool"] not in requested or record.get("repeat", 0) != 0:
            continue
        group = groups.setdefault((benchmark(record), record["bug_type"]), {})
        if (record.get("context_mode", "sensitive") != context_mode or
                record.get("context_limit", 3) != context_limit):
            continue
        if record["tool"] == "usetracessa" and not all(
                record.get(name) == value for name, value in
                dict(REFERENCE, temporal_graph=temporal_graph).items()):
            continue
        if record["tool"] != "saber" and (
                (record.get("pass_calls") is not False) != pass_calls or
                (record.get("svfg_mssa") or "auto") != svfg_mssa or
                record.get("object_granularity") != granularity or
                (record.get("pta") or "andersen") != pta or
                (record.get("object_scope") or "heap") != "heap"):
            continue
        if (record["tool"] == "usetracessa" and
                feasibility_enabled(record) != path_feasibility):
            continue
        limits = saber_budgets if record["tool"] == "saber" else budgets
        if canonical_limits(record["tool"], record.get("search_limits")) != canonical_limits(record["tool"], limits):
            continue
        group.setdefault(config_name(record), []).append(record)

    columns = {name for group in groups.values() for name in group}
    for tool in requested:
        if tool == "usetracessa":
            columns.add(f"usetracessa/batch/{temporal_graph}")
        elif tool == "ufg":
            columns.add("ufg")
        elif tool == "saber" and not any(name.startswith("saber/") for name in columns):
            columns.add("saber/nosmt")
    columns = sorted(columns)
    rows, coverage = [], []
    unverified = 0
    for key, group in sorted(groups.items()):
        states, locations = {}, {}
        for column in columns:
            runs = group.get(column, [])
            state, sites = "NA", set()
            if len(runs) > 1:
                state = "DUP"
            elif runs:
                run = runs[0]
                state = STATUS_LABELS.get(run.get("status"), "ERR")
                if state == "OK":
                    try:
                        details = json.loads(Path(run["details"]).read_text())
                        if run["tool"] == "saber" and not isinstance(details.get("BugTypes"), list):
                            raise ValueError("missing Saber BugTypes array")
                        sites = (saber_locations(run, details) if run["tool"] == "saber"
                                 else ir_locations(run, details))
                        has_alerts = (any(b.get("Valid", True) for kind in details["BugTypes"]
                                          for b in kind.get("Reports", [])) if run["tool"] == "saber"
                                      else bool(details["findings"]))
                        if has_alerts and not sites:
                            raise ValueError("findings have no usable source locations")
                        if (run.get("unknown_incomplete_model") or run.get("model_complete") is False or
                                details.get("status") == "Unknown" or details.get("exhaustive") is False):
                            state = "UNK"
                    except (OSError, ValueError, KeyError, TypeError):
                        state = "PE"
            states[column], locations[column] = state, sites
        # Saber has no SMT UAF client; a global SMT column is not required here.
        required = [c for c in columns if not (c == "saber/smt" and key[1] == "use-after-free")]
        complete = bool(required) and all(states[c] == "OK" for c in required)
        unverified += not complete
        union = set().union(*locations.values()) if locations else set()
        for site in sorted(union):
            flags = [1 if site in locations[c] else 0 if states[c] == "OK" else "NA"
                     for c in columns]
            rows.append(list(key) + [site] + flags + [states[c] for c in columns])
        counts = [len(locations[c]) if states[c] == "OK" else "NA" for c in columns]
        coverage.append(list(key) + counts + [states[c] for c in columns] + [complete])
        both = sum(all(site in locations[c] for c in required) for site in union) if complete else "NA"
        print(f"{key[0]}/{key[1]}: {len(union)} locations, " +
              ", ".join(f"{c}={counts[i]} ({states[c]})" for i, c in enumerate(columns)) +
              f", all={both}")
    chosen = set()
    if sample:
        rng = random.Random(seed)
        by_client = {}
        for index, row in enumerate(rows):
            by_client.setdefault((row[0], row[1]), []).append(index)
        for indices in by_client.values():
            chosen.update(rng.sample(indices, min(sample, len(indices))))
    out = Path(out)
    status_columns = [c + "_status" for c in columns]
    with out.open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["program", "client", "location"] + columns + status_columns +
                        ["sampled", "verdict", "notes"])
        for index, row in enumerate(rows):
            writer.writerow(row + [int(index in chosen), "", ""])
    with out.with_suffix(".coverage.csv").open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["program", "client"] + columns + status_columns + ["complete"])
        writer.writerows(coverage)
    selection = dict(tools=sorted(requested), temporal_graph=temporal_graph,
                     object_granularity=granularity, pta=pta, pass_calls=pass_calls,
                     svfg_mssa=svfg_mssa, context_mode=context_mode, context_limit=context_limit,
                     path_feasibility=path_feasibility, ir_budgets=budgets or {},
                     saber_budgets=saber_budgets or {},
                     lotus_commit=records[0].get("lotus_commit") if records else None,
                     lotus_dirty=records[0].get("lotus_dirty") if records else None)
    out.with_suffix(".selection.json").write_text(json.dumps(selection, indent=2) + "\n")
    print(f"wrote {len(rows)} locations ({len(chosen)} sampled) to {out}; "
          f"unverified program/client groups: {unverified}")
    return 2 if unverified or not groups else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    oracle_parser = sub.add_parser("oracle", help="differential oracle")
    oracle_parser.add_argument("results", type=Path, nargs="+")
    match_parser = sub.add_parser("match", help="cross-tool matching and sampling")
    match_parser.add_argument("results", type=Path, nargs="+")
    match_parser.add_argument("--out", type=Path, default=Path("matches.csv"))
    match_parser.add_argument("--sample", type=int, default=0,
                              help="Locations sampled per program and client")
    match_parser.add_argument("--seed", type=int, default=2027)
    match_parser.add_argument("--pta", choices=("andersen", "fspta"), default="andersen",
                              help="UseTraceSSA/UFG points-to sets to match")
    match_parser.add_argument("--granularity", choices=("site", "abstract"),
                              default="site",
                              help="UseTraceSSA/UFG object granularity to match")
    match_parser.add_argument("--pass-calls", choices=("true", "false"), default="true")
    match_parser.add_argument("--temporal-graph", choices=("sparse", "svfg", "icfg"),
                              default="sparse")
    match_parser.add_argument("--svfg-mssa", choices=("auto", "on", "off"), default="auto")
    match_parser.add_argument("--context", choices=("sensitive", "insensitive"),
                              default="sensitive")
    match_parser.add_argument("--context-limit", default="3",
                              help="Call-string depth, or unlimited")
    match_parser.add_argument("--tools", default="usetracessa,ufg,saber",
                              help="Expected tools; missing runs remain NA")
    add_analysis_options(match_parser)
    args = parser.parse_args(argv)
    try:
        records = load_records(args.results)
    except ValueError as error:
        parser.error(str(error))
    if args.command == "oracle":
        return oracle(records)
    try:
        limit = None if args.context_limit == "unlimited" else int(args.context_limit)
        if limit is not None and limit < 0:
            raise ValueError
    except ValueError:
        parser.error("--context-limit must be a nonnegative integer or unlimited")
    requested = args.tools.split(",")
    if (not requested or len(set(requested)) != len(requested) or
            any(tool not in ("usetracessa", "ufg", "saber") for tool in requested)):
        parser.error("--tools must list distinct names from usetracessa,ufg,saber")
    return match(records, args.out, args.sample, args.seed, args.granularity, args.pta,
                 args.pass_calls == "true", args.temporal_graph, args.svfg_mssa,
                 args.context, limit, requested, args.path_feasibility,
                 selected_limits(args, "usetracessa"), selected_limits(args, "saber"))


if __name__ == "__main__":
    sys.exit(main())
