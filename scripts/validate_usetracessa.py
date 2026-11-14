#!/usr/bin/env python3
"""Validate UTSSA tools, tests and the results pipeline without SPEC inputs."""
import argparse
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from gen_usetracessa_synthetic import add_debug_info

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("lotus-ir-usetracessa", "lotus-ir-ufg", "lotus-check")
FIXTURE = '''
@path = private constant [2 x i8] c"x\\00"
@mode = private constant [2 x i8] c"r\\00"
declare i8* @malloc(i64)
declare void @free(i8*)
declare i8* @fopen(i8*, i8*)
declare i32 @fclose(i8*)
define i32 @main(i1 %close_file) {
entry:
  %p = call i8* @malloc(i64 8)
  %leak = call i8* @malloc(i64 8)
  call void @free(i8* %p)
  call void @free(i8* %p)
  %stale = load i8, i8* %p
  %file = call i8* @fopen(i8* getelementptr ([2 x i8], [2 x i8]* @path, i64 0, i64 0), i8* getelementptr ([2 x i8], [2 x i8]* @mode, i64 0, i64 0))
  br i1 %close_file, label %close, label %exit
close:
  %closed = call i32 @fclose(i8* %file)
  br label %exit
exit:
  ret i32 0
}
'''


def run_step(name, command, directory, allowed_exit=(0,)):
    log = directory / (name + ".log")
    with log.open("w") as output:
        result = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
    print(f"{name}: exit {result.returncode}; log {log}", flush=True)
    if result.returncode not in allowed_exit:
        raise RuntimeError(f"{name} failed:\n" + "\n".join(log.read_text().splitlines()[-25:]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, default=ROOT / "build/bin")
    parser.add_argument("--test-bin-dir", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--smoke-only", action="store_true",
                        help="Runtime-image validation; unit tests need the validation stage")
    parser.add_argument("--expected-commit", help="Require result metadata to identify this exact commit")
    args = parser.parse_args(argv)
    binaries = args.bin_dir.resolve()
    tests = (args.test_bin_dir or binaries / "tests").resolve()
    directory = (args.out_dir or Path(tempfile.mkdtemp(prefix="lotus-validation-"))).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    python = [sys.executable, "-B"]
    try:
        for name in TOOLS:
            binary = binaries / name
            if not binary.is_file() or not os.access(binary, os.X_OK):
                raise RuntimeError(f"missing/non-executable tool: {binary}")
            run_step(name + "-version", [str(binary), "--version"], directory)
        if not args.smoke_only:
            run_step("python-tests", python + ["-m", "unittest", "discover", "-s",
                     str(ROOT / "scripts/tests"), "-p", "test_usetracessa*.py", "-v"], directory)
            for name in ("ir_tests", "alias_tests"):
                run_step(name, [str(tests / name)], directory)
        fixture = directory / "fixture.ll"
        lines = FIXTURE.splitlines()
        # Supply a qualified source identity; basename-only legacy locations
        # cannot establish cross-tool matching coverage.
        add_debug_info(lines, "validation/fixture.c")
        fixture.write_text("\n".join(lines) + "\n")
        results = directory / "runs.json"
        run_step("evaluate", python + [str(ROOT / "scripts/evaluate_usetracessa.py"),
                 "--bin-dir", str(binaries), "--inputs", str(fixture), "--preset", "smoke",
                 "--timeout", "30", "--mem-limit-gb", "2", "--object-modes", "batch,fixed",
                 "--temporal-graphs", "sparse,svfg,icfg", "--object-granularities", "site,abstract",
                 "--pass-calls", "true,false", "--details-dir", str(directory / "details"),
                 "--results", str(results)], directory)
        data = json.loads(results.read_text())
        if args.expected_commit and data["metadata"].get("lotus_commit") != args.expected_commit:
            raise RuntimeError("result metadata does not match --expected-commit")
        if len(data["records"]) != 116 or any(not r.get("details") for r in data["records"]):
            raise RuntimeError("expected 116 runs with details across four clients")
        run_step("oracle", python + [str(ROOT / "scripts/compare_usetracessa.py"),
                 "oracle", str(results)], directory)
        run_step("summarize", python + [str(ROOT / "scripts/summarize_usetracessa.py"),
                 str(results), "--out-dir", str(directory / "summary")], directory)
        run_step("match", python + [str(ROOT / "scripts/compare_usetracessa.py"),
                 "match", str(results), "--out", str(directory / "matches.csv")], directory,
                 allowed_exit=(0, 2))
        # External-library modeling can make negative claims incomplete. That
        # must stay UNK/NA, while each client's known positive remains visible.
        with (directory / "matches.coverage.csv").open() as source:
            coverage = list(csv.DictReader(source))
        expected_status = {"saber/nosmt_status", "ufg_status", "usetracessa/batch/sparse_status"}
        if (len(coverage) != 4 or any(not expected_status.issubset(row) for row in coverage) or
                any(value not in ("OK", "UNK") for row in coverage
                    for key, value in row.items() if key.endswith("_status"))):
            raise RuntimeError("unexpected missing/failed tool in matching coverage")
        with (directory / "matches.csv").open() as source:
            matches = list(csv.DictReader(source))
        clients = {r["bug_type"] for r in data["records"]}
        if ({row["client"] for row in matches} != clients or
                any(not any(row["client"] == client and row.get("usetracessa/batch/sparse") == "1"
                            for row in matches) for client in clients)):
            raise RuntimeError("expected source-located findings for all four clients")
        print(f"Validation passed; results and logs: {directory}")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
