#!/usr/bin/env python3
"""Cross-check evaluate_usetracessa.py runs made with --details-dir.

oracle  Differential oracle (RQ4). For each program, client and object
        granularity, compare UseTraceSSA batch on the sparse graph (the
        reference) with every other UseTraceSSA configuration and with UFG:
        the status of every object and the set of (sink site, object) findings.
        Reports the number of compared queries and lists every mismatch;
        exits with 1 when there is one.

match   Cross-tool alert matching and a manual sampling list. Aligns
        UseTraceSSA, UFG and Saber reports by source file and line: the sink
        for use-after-free, and the allocation for double-free and leaks,
        because Saber reports those once per allocation (all its double-free
        diagnostic steps are the allocation). UseTraceSSA/UFG allocations come
        from the exported object locations; older results without them fall
        back to the sink (double-free) or the witness source (leaks, needs
        --witnesses). Writes a CSV with one row per location and the tools
        reporting it, plus a seeded random sample.

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
    return [record for record in records
            if record.get("status") == "SUCCESS" and record.get("details")]


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
    return (Counter((o["key"], o["status"]) for o in details["objects"]),
            Counter((finding["site"], key) for finding in details["findings"]
                    for key in finding.get("object_keys", [])))


def multiset_diffs(expected, actual, what, other):
    diffs = [f"{what} only in reference: {item} x{count}"
             for item, count in sorted((expected - actual).items())]
    diffs += [f"{what} only in {other}: {item} x{count}"
              for item, count in sorted((actual - expected).items())]
    return diffs


def oracle(records):
    groups = {}
    for record in records:
        if record["tool"] == "saber" or record.get("repeat", 0) != 0:
            continue
        key = (benchmark(record), record["bug_type"], record["object_granularity"])
        groups.setdefault(key, []).append(record)
    compared = mismatches = 0
    for key in sorted(groups):
        runs = groups[key]
        reference = next((run for run in runs if all(
            run.get(name) == value for name, value in REFERENCE.items())), None)
        if reference is None:
            print(f"{'/'.join(map(str, key))}: no reference run, skipped")
            continue
        expected = json.loads(Path(reference["details"]).read_text())
        statuses = {o["object"]: o["status"] for o in expected["objects"]}
        expected_findings = findings(expected)
        for run in runs:
            if run is reference:
                continue
            actual = json.loads(Path(run["details"]).read_text())
            actual_statuses = {o["object"]: o["status"] for o in actual["objects"]}
            actual_findings = findings(actual)
            objects = set(statuses) | set(actual_statuses)
            queries = max(len(statuses), len(actual_statuses))
            compared += queries
            note = ""
            expected_keys, actual_keys = keyed(expected), keyed(actual)
            if (set(statuses) != set(actual_statuses) and expected_keys and actual_keys):
                # Abstract SVFG object IDs can differ between processes; the
                # exported keys name objects independently of the run.
                note = " (object IDs differ between runs; compared by object keys)"
                diffs = multiset_diffs(expected_keys[0], actual_keys[0], "object",
                                       config_name(run))
                diffs += multiset_diffs(expected_keys[1], actual_keys[1], "finding",
                                        config_name(run))
            elif set(statuses) != set(actual_statuses) and len(statuses) == len(actual_statuses):
                # Results without object keys: abstract SVFG object IDs are not stable;
                # site-granularity IDs are. Compare what does not name objects.
                note = " (object IDs differ between runs; compared by status counts)"
                diffs = [f"{status} objects: {count} vs "
                         f"{Counter(actual_statuses.values())[status]}"
                         for status, count in Counter(statuses.values()).items()
                         if count != Counter(actual_statuses.values())[status]]
                sites = Counter(site for site, _ in expected_findings)
                actual_sites = Counter(site for site, _ in actual_findings)
                diffs += [f"site {site}: {sites[site]} vs {actual_sites[site]} objects"
                          for site in sorted(set(sites) | set(actual_sites))
                          if sites[site] != actual_sites[site]]
            else:
                diffs = [f"object {obj}: {statuses.get(obj)} vs {actual_statuses.get(obj)}"
                         for obj in sorted(objects)
                         if statuses.get(obj) != actual_statuses.get(obj)]
                diffs += [f"finding only in reference: {site} object {obj}"
                          for site, obj in sorted(expected_findings - actual_findings)]
                diffs += [f"finding only in {config_name(run)}: {site} object {obj}"
                          for site, obj in sorted(actual_findings - expected_findings)]
            name = "/".join(map(str, key)) + f" vs {config_name(run)}"
            print(f"{name}: {queries} objects, "
                  f"{len(actual_findings)} findings, {len(diffs)} mismatches{note}")
            for diff in diffs:
                print(f"  {diff}")
            mismatches += len(diffs)
    print(f"compared queries (object x configuration): {compared}; "
          f"mismatches: {mismatches}")
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


def match(records, out, sample, seed, granularity="site"):
    table = {}
    for record in records:
        if record.get("repeat", 0) != 0:
            continue
        if record["tool"] == "usetracessa" and not all(
                record.get(name) == value for name, value in REFERENCE.items()):
            continue
        if record["tool"] != "saber" and record.get("object_granularity") != granularity:
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


def main():
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
    match_parser.add_argument("--granularity", choices=("site", "abstract"),
                              default="site",
                              help="UseTraceSSA/UFG object granularity to match")
    args = parser.parse_args()
    records = load_records(args.results)
    if args.command == "oracle":
        return oracle(records)
    return match(records, args.out, args.sample, args.seed, args.granularity)


if __name__ == "__main__":
    sys.exit(main())
