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

REFERENCE = {"tool": "usetracessa", "object_mode": "batch", "temporal_graph": "sparse"}
LEAKS = ("memory-leak", "file-leak")
# Saber reports these once per allocation; align every tool by allocation.
BY_ALLOCATION = LEAKS + ("double-free",)


def load_records(paths):
    records = []
    for path in paths:
        data = json.loads(Path(path).read_text())
        records += data["records"] if isinstance(data, dict) else data
    # Keep failures: an oracle must not turn missing coverage into success.
    return records


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
          context_mode="sensitive", context_limit=3):
    table = {}
    for record in records:
        if record.get("status") != "SUCCESS" or not record.get("details"):
            continue
        if record.get("repeat", 0) != 0:
            continue
        if (record.get("context_mode", "sensitive") != context_mode or
                record.get("context_limit", 3) != context_limit):
            continue
        if record["tool"] == "usetracessa" and not all(
                record.get(name) == value for name, value in
                dict(REFERENCE, temporal_graph=temporal_graph).items()):
            continue
        if record["tool"] != "saber" and (
                (record.get("pass_calls") is not False) != pass_calls or
                (record.get("svfg_mssa") or "auto") != svfg_mssa):
            continue
        if record["tool"] != "saber" and record.get("object_granularity") != granularity:
            continue
        if record["tool"] != "saber" and (record.get("pta") or "andersen") != pta:
            continue
        if record["tool"] != "saber" and (record.get("object_scope") or "heap") != "heap":
            continue
        details = json.loads(Path(record["details"]).read_text())
        sites = (saber_locations(record, details) if record["tool"] == "saber"
                 else ir_locations(record, details))
        for site in sites:
            key = (benchmark(record), record["bug_type"], site)
            table.setdefault(key, set()).add(config_name(record))
    tools = sorted({tool for reported in table.values() for tool in reported})
    rows = []
    for key in sorted(table):
        reported = table[key]
        rows.append(list(key) + [int(tool in reported) for tool in tools])
    chosen = set()
    if sample:
        rng = random.Random(seed)
        by_client = {}
        for index, row in enumerate(rows):
            by_client.setdefault((row[0], row[1]), []).append(index)
        for indices in by_client.values():
            chosen.update(rng.sample(indices, min(sample, len(indices))))
    with open(out, "w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["program", "client", "location"] + tools +
                        ["sampled", "verdict", "notes"])
        for index, row in enumerate(rows):
            writer.writerow(row + [int(index in chosen), "", ""])
    summary = {}
    for row in rows:
        summary.setdefault((row[0], row[1]), []).append(row[3:])
    for (program, client), flags in sorted(summary.items()):
        counts = {tool: sum(flag[i] for flag in flags) for i, tool in enumerate(tools)}
        both = sum(all(flag) for flag in flags)
        print(f"{program}/{client}: {len(flags)} locations, " +
              ", ".join(f"{tool}={count}" for tool, count in counts.items()) +
              f", all={both}")
    print(f"wrote {len(rows)} locations ({len(chosen)} sampled) to {out}")
    return 0


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
    args = parser.parse_args(argv)
    records = load_records(args.results)
    if args.command == "oracle":
        return oracle(records)
    try:
        limit = None if args.context_limit == "unlimited" else int(args.context_limit)
        if limit is not None and limit < 0:
            raise ValueError
    except ValueError:
        parser.error("--context-limit must be a nonnegative integer or unlimited")
    return match(records, args.out, args.sample, args.seed, args.granularity, args.pta,
                 args.pass_calls == "true", args.temporal_graph, args.svfg_mssa,
                 args.context, limit)


if __name__ == "__main__":
    sys.exit(main())
