#!/usr/bin/env python3
"""Rebuild the SV-COMP function corpus used by the CAV'21 DOD evaluation.

Chalupa et al. (CAV 2021, LNCS 12760, Section 6.1) built their corpus from the
SV-COMP 2020 benchmarks (github.com/sosy-lab/sv-benchmarks, tag svcomp20):
every source file was compiled with clang to LLVM and preprocessed by
-lowerswitch, individual functions with fewer than 100 basic blocks were
dropped, duplicates were removed, and 2440 functions were left.  They report a
nonempty DOD relation for 12 of them.

This script reproduces that corpus so that the biclique algorithms can be
compared on real functions whose order relation is nonempty, instead of only on
generated families.  Two deviations from the paper are deliberate and reported
in the summary:

* When a source file has a preprocessed sibling (``foo.i`` next to ``foo.c``)
  only the preprocessed one is compiled: it needs no include path, and
  compiling both would enter the same function twice.
* Duplicates are removed by function name plus vertex, edge and decision
  counts.  The paper also compares DFS edge classes and DFS tree depth, which
  the driver does not report, so this test is coarser and may merge slightly
  more functions.

Bitcode is kept only for modules that contribute at least one function of the
required size; everything else is compiled to a temporary file and deleted.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence


def source_files(corpus: Path) -> list[Path]:
    """Preprocessed files, plus the sources that have no preprocessed sibling."""

    preprocessed = {p.with_suffix("") for p in corpus.rglob("*.i")}
    files = sorted(corpus.rglob("*.i"))
    files += sorted(p for p in corpus.rglob("*.c") if p.with_suffix("") not in preprocessed)
    return files


def scan_one(
    source: Path, clang: Path, tool: Path, keep_dir: Path, min_blocks: int,
    timeout: float, target: str, extra_args: Sequence[str], sysroot: str | None,
    opt_level: str,
) -> dict:
    """Compile one source file and measure every function in the module."""

    result: dict = {"source": str(source), "rows": [], "functions": 0, "nonempty": []}
    with tempfile.TemporaryDirectory() as work:
        bitcode = Path(work) / "module.bc"
        # Two kinds of input need two targets, and neither works for both. The
        # preprocessed files carry x86 inline assembly, which only parses for an
        # x86 triple, and they need no system headers. The plain sources include
        # system headers, which exist only for the host triple on this machine.
        # Try the likely one first and fall back.
        targets = ([target, None] if source.suffix == ".i" else [None, target])
        compiled = None
        for candidate in targets:
            # The host attempt needs the macOS SDK explicitly: this clang is not
            # the system one and does not find /usr/include on its own.
            flags = [f"--target={candidate}"] if candidate else (
                ["-isysroot", sysroot] if sysroot else [])
            compiled = subprocess.run(
                [str(clang), *flags, "-emit-llvm", "-c", f"-{opt_level}", "-g0", "-w",
                 *extra_args, "-o", str(bitcode), str(source)],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
            if compiled.returncode == 0 and bitcode.is_file():
                result["target_used"] = candidate or "host"
                break
        if compiled is None or compiled.returncode != 0 or not bitcode.is_file():
            result["error"] = "compile"
            result["detail"] = [
                line for line in (compiled.stderr.splitlines() if compiled else [])
                if ": error:" in line or ": fatal error:" in line
            ][:1]
            return result
        try:
            measured = subprocess.run(
                [str(tool), str(bitcode), "--algorithm=dod-compact", "--visit-pairs",
                 "--lower-switch=true", "--format=csv"],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            result["error"] = "driver-timeout"
            return result
        if measured.returncode != 0:
            result["error"] = "driver"
            result["detail"] = measured.stderr.strip().splitlines()[:1]
            return result

        rows = list(csv.DictReader(io.StringIO(measured.stdout)))
        result["functions"] = len(rows)
        big = [r for r in rows if int(r["nodes"]) >= min_blocks]
        # A nonempty DOD relation is the point of this corpus, so record it at
        # any size: the paper's >=100-block filter was for run time only.
        result["nonempty"] = [
            {"function": r["function"].strip('"'), "nodes": int(r["nodes"]),
             "edges": int(r["edges"]), "decisions": int(r["decisions"]),
             "bicliques": int(r["bicliques"]), "dod_pairs": int(r["dod_pairs"]),
             "source": str(source)}
            for r in rows if int(r["dod_pairs"]) > 0
        ]
        if not big and not result["nonempty"]:
            return result
        # Keep the bitcode: the paired SOTA/Full comparison reruns these
        # functions with --function=<name>.
        # Stable name: Python's hash() is salted per process.
        digest = hashlib.sha1(str(source).encode()).hexdigest()[:16]
        target = keep_dir / f"{digest}.bc"
        target.write_bytes(bitcode.read_bytes())
        for row in big:
            result["rows"].append({
                "module": str(target), "source": str(source), "function": row["function"].strip('"'),
                "nodes": int(row["nodes"]), "edges": int(row["edges"]),
                "decisions": int(row["decisions"]), "bicliques": int(row["bicliques"]),
                "dod_pairs": int(row["dod_pairs"]),
            })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True,
                        help="path to the sv-benchmarks c/ directory (tag svcomp20)")
    parser.add_argument("--work-dir", type=Path, required=True,
                        help="directory for kept bitcode and the CSV outputs")
    parser.add_argument("--clang", type=Path,
                        default=Path("/opt/homebrew/opt/llvm@14/bin/clang"))
    parser.add_argument("--tool", type=Path,
                        default=Path("build-release/bin/lotus-ir-control-dependence"))
    parser.add_argument("--min-blocks", type=int, default=100,
                        help="keep functions with at least this many basic blocks")
    parser.add_argument("--target", default="x86_64-unknown-linux-gnu",
                        help=("non-host clang target triple, tried first for preprocessed "
                              "files: they carry x86 inline assembly that does not parse "
                              "for the host triple. Plain sources are tried with the host "
                              "triple first, because system headers exist only there."))
    parser.add_argument("--opt-level", default="O0", choices=("O0", "O1", "O2", "O3", "Os"),
                        help=("clang optimization level. The paper does not state one. At O0 "
                              "the frontend emits reducible CFGs almost everywhere; passes "
                              "such as jump threading and tail duplication can introduce "
                              "irreducible loops, which is where DOD becomes nonempty."))
    parser.add_argument("--sysroot", default=None,
                        help=("passed as -isysroot for host-target compiles; defaults to "
                              "xcrun --show-sdk-path on macOS, where this clang finds no "
                              "system headers on its own"))
    parser.add_argument("--include-dir", type=Path, action="append", default=[],
                        help=("added as -I; the Juliet test cases include "
                              "std_testcase.h from their support directory"))
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after this many source files (0 = all)")
    args = parser.parse_args()

    files = source_files(args.corpus)
    if args.limit:
        files = files[: args.limit]
    keep_dir = args.work_dir / "bitcode"
    keep_dir.mkdir(parents=True, exist_ok=True)

    extra_args = [arg for directory in args.include_dir for arg in ("-I", str(directory))]
    sysroot = args.sysroot
    if sysroot is None and sys.platform == "darwin":
        probe = subprocess.run(["xcrun", "--show-sdk-path"], capture_output=True, text=True,
                               check=False)
        sysroot = probe.stdout.strip() or None
        if not sysroot:
            # xcrun fails when this interpreter runs under a different
            # architecture than the command-line tools; look for the SDK.
            for candidate in (
                "/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk",
                "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform"
                "/Developer/SDKs/MacOSX.sdk",
            ):
                if Path(candidate).is_dir():
                    sysroot = candidate
                    break
    started = time.time()
    rows: list[dict] = []
    nonempty_any: list[dict] = []
    by_family: dict[str, dict[str, int]] = {}
    by_target: dict[str, int] = {}
    failures: dict[str, int] = {}
    failure_examples: dict[str, list] = {}
    total_functions = 0
    done = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [
            pool.submit(scan_one, f, args.clang, args.tool, keep_dir, args.min_blocks,
                        args.timeout, args.target, extra_args, sysroot, args.opt_level)
            for f in files
        ]
        for future in futures:
            try:
                outcome = future.result()
            except Exception as error:  # noqa: BLE001 - reported, not raised
                failures["harness"] = failures.get("harness", 0) + 1
                failure_examples.setdefault("harness", []).append(str(error))
                continue
            done += 1
            total_functions += outcome["functions"]
            rows.extend(outcome["rows"])
            nonempty_any.extend(outcome["nonempty"])
            if "target_used" in outcome:
                by_target[outcome["target_used"]] = by_target.get(outcome["target_used"], 0) + 1
            family = Path(outcome["source"]).relative_to(args.corpus).parts[0]
            stats = by_family.setdefault(family, {"files": 0, "compiled": 0, "failed": 0})
            stats["files"] += 1
            stats["compiled" if "error" not in outcome else "failed"] += 1
            if "error" in outcome:
                kind = outcome["error"]
                failures[kind] = failures.get(kind, 0) + 1
                if len(failure_examples.setdefault(kind, [])) < 3:
                    failure_examples[kind].append(
                        {"source": outcome["source"], "detail": outcome.get("detail")})
            if done % 2000 == 0:
                print(f"[{done}/{len(files)}] {time.time() - started:.0f}s, "
                      f"{len(rows)} large functions", file=sys.stderr, flush=True)

    # Deduplicate: same name and the same measured shape.
    unique: dict[tuple, dict] = {}
    for row in rows:
        unique.setdefault(
            (row["function"], row["nodes"], row["edges"], row["decisions"]), row)
    deduped = sorted(unique.values(), key=lambda r: (-r["nodes"], r["function"]))
    nonempty = [r for r in deduped if r["dod_pairs"] > 0]

    functions_csv = args.work_dir / "corpus_functions.csv"
    with functions_csv.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(deduped[0]) if deduped else
                                ["module", "source", "function", "nodes", "edges",
                                 "decisions", "bicliques", "dod_pairs"])
        writer.writeheader()
        writer.writerows(deduped)

    summary = {
        "corpus": str(args.corpus),
        "source_files": len(files),
        "source_files_scanned": done,
        "failures": failures,
        "failure_examples": failure_examples,
        "functions_seen": total_functions,
        "functions_at_least_min_blocks": len(rows),
        "functions_after_dedup": len(deduped),
        "functions_with_nonempty_dod": len(nonempty),
        "functions_with_nonempty_dod_any_size": len(nonempty_any),
        "nonempty_dod_any_size": nonempty_any[:200],
        "by_family": dict(sorted(by_family.items(), key=lambda kv: -kv[1]["files"])),
        "include_dirs": [str(d) for d in args.include_dir],
        "compiled_by_target": by_target,
        "sysroot": sysroot,
        "nonempty_dod_functions": [
            {k: r[k] for k in ("function", "nodes", "edges", "decisions", "bicliques", "dod_pairs", "source")}
            for r in nonempty
        ],
        "min_blocks": args.min_blocks,
        "opt_level": args.opt_level,
        "target": args.target,
        "seconds": round(time.time() - started, 1),
    }
    (args.work_dir / "corpus_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items()
                      if k not in ("nonempty_dod_functions", "failure_examples",
                                   "nonempty_dod_any_size", "by_family")}, indent=2))
    print(f"Written: {functions_csv}")
    print(f"Written: {args.work_dir / 'corpus_summary.json'}")


if __name__ == "__main__":
    main()
