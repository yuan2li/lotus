#!/usr/bin/env python3
"""Compare UseTraceSSA, object-expanded UFG, and Saber on LLVM bitcode.

UseTraceSSA, UFG, and Saber run double-free, use-after-free, memory-leak, and
file-leak (select with --checks). Saber runs without SMT path validation by
default; --saber-smt adds SMT runs for its leak, double-free, and file checks.
The IR leak clients have limited ownership-escape modeling.
Every tool reports split timings: parse, SVFG construction, and analysis
(`analysis_ms` = total - parse - SVFG). Runs are serial by default so wall time
and peak RSS are easier to compare; --max-workers enables throughput-oriented
parallel runs. Tools run with the Lotus root as working directory so that
config/*.spec are found.

Examples:
    python3 scripts/evaluate_usetracessa.py
    python3 scripts/evaluate_usetracessa.py --tools usetracessa,ufg \
        --inputs /path/to/example.bc --results /tmp/example-eval.json
    python3 scripts/evaluate_usetracessa.py --tools usetracessa \
        --object-modes batch,fixed --object-granularities site,abstract
"""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BINS = {
    "usetracessa": ROOT / "build/bin/lotus-ir-usetracessa",
    "ufg": ROOT / "build/bin/lotus-ir-ufg",
    "saber": ROOT / "build/bin/lotus-check",
}
SHARED_CHECKS = ("double-free", "use-after-free", "memory-leak", "file-leak")
SABER_CHECKS = ("double-free", "use-after-free", "memory-leak", "file-leak")
# Saber's UAF checker has no SMT path validation.
SABER_SMT_CHECKS = ("double-free", "memory-leak", "file-leak")
OBJECT_MODES = ("batch", "fixed")
OBJECT_GRANULARITIES = ("site", "abstract")
SVFG_MSSA_MODES = ("auto", "on", "off")
BITCODE_MAGIC = (b"BC\xc0\xde", b"\xde\xc0\x17\x0b")
KEY_VALUE = re.compile(r"([a-z][a-z0-9_]*)=([^\s]+)")
IR_BUDGETS = ("max-product-states", "max-summary-pairs")


def budget_value(text):
    if text == "unlimited":
        return 0
    if not re.fullmatch(r"[0-9]+", text) or int(text) > 2**64 - 1:
        raise argparse.ArgumentTypeError("expected a nonnegative integer or unlimited")
    return int(text)


def context_limit_value(text):
    # Unlike resource budgets, context depth 0 retains its legacy meaning.
    return None if text == "unlimited" else budget_value(text)


def key_values(line):
    return dict(KEY_VALUE.findall(line))


def choice_list(choices):
    def parse(text):
        values = tuple(part.strip() for part in text.split(",") if part.strip())
        if not values or len(set(values)) != len(values) or any(
                value not in choices for value in values):
            raise argparse.ArgumentTypeError(
                f"expected distinct comma-separated values from {','.join(choices)}")
        return values
    return parse


def is_llvm_input(path):
    """LLVM bitcode by magic (SPEC2017 files have no suffix) or textual .ll."""
    if path.suffix == ".ll":
        return True
    try:
        with path.open("rb") as handle:
            return handle.read(4) in BITCODE_MAGIC
    except OSError:
        return False


def get_process_rss_mb(pid):
    """Current RSS for the direct tool process, in MiB."""
    status = Path(f"/proc/{pid}/status")
    try:
        if status.exists():
            match = re.search(r"^VmRSS:\s+(\d+)\s+kB", status.read_text(), re.MULTILINE)
            return int(match.group(1)) / 1024.0 if match else 0.0
        output = subprocess.check_output(
            ["ps", "-o", "rss=", "-p", str(pid)], stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return float(output) / 1024.0 if output else 0.0
    except (OSError, ValueError, subprocess.CalledProcessError):
        return 0.0


def kill_proc_group(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


def reap_with_usage(proc, options):
    pid, status, usage = os.wait4(proc.pid, options)
    if pid == 0:
        return None
    proc.returncode = os.waitstatus_to_exitcode(status)
    return usage


def peak_rss_mb(usage):
    if usage is None:
        return 0.0
    # Darwin reports bytes; Linux reports KiB.
    divisor = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0
    return usage.ru_maxrss / divisor


def run_cmd_with_limits(cmd, timeout_s, mem_limit_gb):
    start = time.monotonic()
    with tempfile.TemporaryFile(mode="w+t") as stdout_file, \
            tempfile.TemporaryFile(mode="w+t") as stderr_file:
        try:
            proc = subprocess.Popen(
                cmd, stdout=stdout_file, stderr=stderr_file,
                start_new_session=True, cwd=ROOT,
            )
        except OSError as error:
            return {
                "status": "ERROR(start)", "wall_time_s": 0.0,
                "max_rss_mb": 0.0, "stdout": "", "stderr": str(error),
            }
        peak_rss = 0.0
        killed_reason = None
        usage = None
        while True:
            usage = reap_with_usage(proc, os.WNOHANG)
            if usage is not None:
                break
            peak_rss = max(peak_rss, get_process_rss_mb(proc.pid))
            elapsed = time.monotonic() - start
            if elapsed > timeout_s:
                killed_reason = "TIMEOUT"
            elif peak_rss > mem_limit_gb * 1024.0:
                killed_reason = "OOM"
            if killed_reason:
                kill_proc_group(proc)
                break
            time.sleep(0.1)
        if usage is None:
            usage = reap_with_usage(proc, 0)
        peak_rss = max(peak_rss, peak_rss_mb(usage))
        if not killed_reason and peak_rss > mem_limit_gb * 1024.0:
            killed_reason = "OOM"
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read()
        stderr = stderr_file.read()
    status = killed_reason or ("SUCCESS" if proc.returncode == 0
                               else f"ERROR(rc={proc.returncode})")
    return {
        "status": status,
        "wall_time_s": round(time.monotonic() - start, 3),
        "max_rss_mb": round(peak_rss, 2),
        "stdout": stdout,
        "stderr": stderr,
    }


def parse_ir_output(stdout, stderr):
    """Parse key=value lines without relying on field order or adjacency."""
    parsed = {"check_result": None, "findings": None,
              "finding_sites": None, "exhaustive": None}
    for line in stdout.splitlines():
        if line.startswith(("svfg_nodes=", "batch_products=",
                            "lane_product_states=")):
            for key, value in key_values(line).items():
                if key == "context_mode":
                    parsed[key] = value
                    continue
                try:
                    parsed[key] = int(value)
                except ValueError:
                    pass
        elif line.startswith("check="):
            fields = key_values(line)
            parsed["check_result"] = fields.get("result")
            for key in ("findings", "finding_sites"):
                if key in fields:
                    parsed[key] = int(fields[key])
            if "exhaustive" in fields:
                parsed["exhaustive"] = fields["exhaustive"] == "yes"
            for key in ("search_complete", "model_complete"):
                if key in fields:
                    parsed[key] = fields[key] in ("true", "yes")
            if "stop_reason" in fields:
                parsed["stop_reason"] = fields["stop_reason"]
            for key in ("budget_limit", "budget_observed"):
                if key in fields:
                    parsed[key] = int(fields[key])
    parse_timing(stderr, parsed)
    return parsed


def parse_timing(stderr, parsed):
    for line in stderr.splitlines():
        if not line.startswith("timing_ms "):
            continue
        for key, value in key_values(line).items():
            try:
                parsed[f"{key}_ms"] = float(value)
            except ValueError:
                pass
    # Decision 4: compare analysis time without the shared frontend (parse and
    # SVFG construction, including Saber's Memory SSA).
    if all(f"{key}_ms" in parsed for key in ("total", "parse", "svfg")):
        parsed["analysis_ms"] = round(
            parsed["total_ms"] - parsed["parse_ms"] - parsed["svfg_ms"], 3)


def parse_saber_output(stdout, stderr):
    parsed = {"findings": None}
    output = stdout + "\n" + stderr
    for field in ("elapsed-ms", "functions", "instructions"):
        match = re.search(rf"{re.escape(field)}:\s+(\d+)", output)
        if match:
            parsed[field.replace("-", "_")] = int(match.group(1))
    match = re.search(r"Findings \((\d+)\)", output)
    if match:
        parsed["findings"] = int(match.group(1))
    else:
        match = re.search(r"\bfindings:\s*(\d+)", output)
        if match:
            parsed["findings"] = int(match.group(1))
        elif "No findings." in output:
            parsed["findings"] = 0
    parse_timing(stderr, parsed)
    return parsed


def build_cmd(task, binaries, context_limit, budgets=None,
              context_mode="sensitive", saber_budgets=None):
    tool, bug_type, bc_path = task["tool"], task["bug_type"], task["bc_path"]
    if tool in ("usetracessa", "ufg"):
        cmd = [str(binaries[tool]), str(bc_path), "--quiet", "--timing",
               f"--check={bug_type}", f"--context={context_mode}",
               f"--context-limit={context_limit if context_limit is not None else 'unlimited'}",
               f"--object-granularity={task['object_granularity']}",
               f"--svfg-mssa={task['svfg_mssa']}"]
        if tool == "usetracessa":
            cmd.append(f"--object-mode={task['object_mode']}")
        for flag in IR_BUDGETS:
            limit = (budgets or {}).get(flag, 0)
            cmd.append(f"--{flag}={limit if limit else 'unlimited'}")
        return cmd
    if context_mode != "sensitive" or context_limit is None:
        raise ValueError("Saber requires context-sensitive search with a numeric context limit")
    return [str(binaries["saber"]), "--engine=saber",
            f"--checks={bug_type}", "--analysis-stats", "--saber.timing",
            f"--saber.context-limit={context_limit}",
            *(f"--saber.{key}={value}" for key, value in (saber_budgets or {}).items()),
            *(() if task["smt_mode"] == "smt" else ("--saber.no-smt",)),
            str(bc_path)]


def make_tasks(benchmarks, tools, checks, object_modes=("batch",),
               granularities=("site",), svfg_mssa="auto", saber_smt=False):
    """Expand tools into configurations; options that do not apply stay None."""
    tasks = []
    for bc_path in benchmarks:
        for bug_type in checks:
            for tool in ("usetracessa", "ufg"):
                if tool not in tools:
                    continue
                for granularity in granularities:
                    for object_mode in (object_modes if tool == "usetracessa"
                                        else (None,)):
                        tasks.append({
                            "tool": tool, "bug_type": bug_type, "bc_path": bc_path,
                            "smt_mode": "nosmt", "object_mode": object_mode,
                            "object_granularity": granularity,
                            "svfg_mssa": svfg_mssa})
            if bug_type in SABER_CHECKS and "saber" in tools:
                smt_modes = ("nosmt", "smt") if (
                    saber_smt and bug_type in SABER_SMT_CHECKS) else ("nosmt",)
                for smt_mode in smt_modes:
                    tasks.append({
                        "tool": "saber", "bug_type": bug_type, "bc_path": bc_path,
                        "smt_mode": smt_mode, "object_mode": None,
                        "object_granularity": None, "svfg_mssa": None})
    return tasks


def execute_task(index, task, timeout_s, mem_limit_gb, binaries,
                 context_limit, budgets=None, context_mode="sensitive", saber_budgets=None):
    tool, bug_type, bc_path = task["tool"], task["bug_type"], task["bc_path"]
    cmd = build_cmd(task, binaries, context_limit, budgets, context_mode,
                    saber_budgets)
    execution = run_cmd_with_limits(cmd, timeout_s, mem_limit_gb)
    parsed = (parse_saber_output(execution["stdout"], execution["stderr"])
              if tool == "saber" else
              parse_ir_output(execution["stdout"], execution["stderr"]))
    status = execution["status"]
    if (tool != "saber" and status in ("SUCCESS", "ERROR(rc=2)") and
            parsed.get("search_complete") is False):
        status = "INCOMPLETE"
    if status in ("SUCCESS", "INCOMPLETE"):
        required = ("findings",) if tool == "saber" else (
            "check_result", "findings", "finding_sites", "total_ms",
        )
        if any(parsed.get(field) is None for field in required):
            status = "PARSE_ERROR"
    record = {
        "task_id": index, "tool": tool, "bug_type": bug_type,
        "smt_mode": task["smt_mode"],
        "object_mode": task["object_mode"],
        "object_granularity": task["object_granularity"],
        "svfg_mssa": task["svfg_mssa"],
        "bc": bc_path.name, "bc_path": str(bc_path),
        "analysis_scope": ("context-bounded-icfg-candidate"
                           if tool == "saber" and bug_type == "use-after-free"
                           else "exit-candidate-limited-escape-model"
                           if tool != "saber" and bug_type in ("memory-leak", "file-leak")
                           else "tool-default"),
        "context_semantics": ("insensitive" if context_mode == "insensitive" else
                              f"call-string-k={context_limit}"
                              if context_limit is not None
                              else "unbounded-realizable"),
        "comparison_group": ("system-baseline" if tool == "saber"
                             else "representation"),
        "finding_unit": ("bug-report" if tool == "saber"
                         else "sink-with-object-set"),
        "context_limit": context_limit,
        "context_mode": context_mode,
        "search_limits": ({key: (budgets or {}).get(key, 0) for key in IR_BUDGETS}
                          if tool != "saber" else saber_budgets or {}),
        "command": cmd, "status": status,
        "wall_time_s": execution["wall_time_s"],
        "max_rss_mb": execution["max_rss_mb"],
        **parsed,
    }
    if status != "SUCCESS":
        record["stdout_tail"] = execution["stdout"][-2000:]
        record["stderr_tail"] = execution["stderr"][-2000:]
    return record


def save_results(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, prefix=f".{path.name}.",
        suffix=".tmp", delete=False,
    ) as output:
        json.dump([item for item in records if item is not None], output, indent=2)
        output.write("\n")
        temporary = Path(output.name)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate UseTraceSSA, object-expanded UFG, and Saber",
        allow_abbrev=False)
    parser.add_argument("--tools", default="usetracessa,ufg,saber",
                        help="Comma-separated tools (default: all three)")
    parser.add_argument("--max-workers", type=int, default=1,
                        help="Concurrent runs (default: 1 for timing comparisons)")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="Per-run timeout in seconds (default: 300)")
    parser.add_argument("--mem-limit-gb", type=float, default=6.0,
                        help="Sampled direct-process RSS limit in GiB (default: 6)")
    context_options = parser.add_mutually_exclusive_group()
    context_options.add_argument("--context-limit", "--context-depth",
                                 type=context_limit_value, default=3,
                                 help="Call-string depth: N or unlimited (default: 3; "
                                      "0 immediately merges contexts; used with sensitive context)")
    context_options.add_argument("--unbounded-context", action="store_true",
                                 help="Alias for --context-limit=unlimited; requires "
                                      "--tools without Saber")
    parser.add_argument("--context", choices=("sensitive", "insensitive"),
                        default="sensitive", help="Call/return matching (default: sensitive); "
                        "independent of resource budgets")
    for flag in IR_BUDGETS:
        parser.add_argument(f"--{flag}", type=budget_value, default=0,
                            help="UseTraceSSA/UFG per-query budget: N or unlimited (default)")
    parser.add_argument("--saber-max-forward-items", type=budget_value, default=None,
                        help="Override Saber forward-item limit (0/unlimited disables it)")
    parser.add_argument("--saber-solver-timeout-ms", type=budget_value, default=None,
                        help="Override Saber per-solver timeout (0/unlimited disables it)")
    parser.add_argument("--checks", type=choice_list(SHARED_CHECKS),
                        default=SHARED_CHECKS,
                        help="Comma-separated clients (default: all four; decision 2 "
                             "reports memory-leak and file-leak as the leak client)")
    parser.add_argument("--object-modes", type=choice_list(OBJECT_MODES),
                        default=("batch",),
                        help="UseTraceSSA search: batch, fixed, or both (default: batch)")
    parser.add_argument("--object-granularities", type=choice_list(OBJECT_GRANULARITIES),
                        default=("site",),
                        help="UseTraceSSA/UFG resource objects: site, abstract, or both "
                             "(default: site)")
    parser.add_argument("--svfg-mssa", choices=SVFG_MSSA_MODES, default="auto",
                        help="UseTraceSSA/UFG SVFG Memory SSA (default: auto, off for "
                             "checks; on is a sensitivity configuration)")
    parser.add_argument("--saber-smt", action="store_true",
                        help="Also run Saber with SMT path validation for leak, "
                             "double-free, and file checks (RQ4 only)")
    parser.add_argument("--benchmarks-dir", type=Path,
                        default=ROOT / "benchmarks/real-world/SPEC2017",
                        help="Directory containing LLVM bitcode files")
    parser.add_argument("--inputs", type=Path, nargs="+",
                        help="Explicit LLVM bitcode files instead of --benchmarks-dir")
    parser.add_argument("--results", type=Path, default=Path("eval_results.json"))
    for tool in DEFAULT_BINS:
        parser.add_argument(f"--{tool}-bin", type=Path, default=DEFAULT_BINS[tool])
    args = parser.parse_args()

    tools = tuple(part.strip() for part in args.tools.split(",") if part.strip())
    if not tools or len(set(tools)) != len(tools) or any(
            tool not in DEFAULT_BINS for tool in tools):
        parser.error("--tools must list distinct names from usetracessa,ufg,saber")
    if args.max_workers < 1 or args.timeout <= 0 or args.mem_limit_gb <= 0:
        parser.error("workers, timeout, and memory limit must be positive")
    context_mode = args.context
    context_limit = None if args.unbounded_context else args.context_limit
    if "saber" in tools:
        if context_mode != "sensitive" or context_limit is None:
            parser.error("Saber requires --context=sensitive and a numeric --context-limit")
        if context_limit > 2**32 - 1:
            parser.error("--context-limit exceeds Saber's unsigned option range")
    budgets = {flag: getattr(args, flag.replace('-', '_')) for flag in IR_BUDGETS}
    saber_budgets = {}
    for flag in ("max-forward-items", "solver-timeout-ms"):
        value = getattr(args, 'saber_' + flag.replace('-', '_'))
        if value is not None:
            if value > 2**32 - 1:
                parser.error(f"--saber-{flag} exceeds Saber's unsigned option range")
            saber_budgets[flag] = value
    binaries = {tool: getattr(args, f"{tool}_bin").resolve() for tool in tools}
    for tool, binary in binaries.items():
        if not binary.is_file():
            parser.error(f"{tool} binary not found: {binary}")

    if args.inputs:
        benchmarks = [path.resolve() for path in args.inputs]
    else:
        directory = args.benchmarks_dir.resolve()
        if not directory.is_dir():
            parser.error(f"benchmark directory not found: {directory}")
        benchmarks = sorted(path for path in directory.iterdir()
                            if path.is_file() and not path.name.startswith(".")
                            and is_llvm_input(path))
    if not benchmarks or any(not path.is_file() for path in benchmarks):
        parser.error("no valid benchmark bitcode files were selected")

    tasks = make_tasks(benchmarks, tools, args.checks, args.object_modes,
                       args.object_granularities, args.svfg_mssa, args.saber_smt)
    records = [None] * len(tasks)
    print(f"Benchmarks: {len(benchmarks)}  Tools: {','.join(tools)}")
    print(f"Tasks: {len(tasks)}  Workers: {args.max_workers}")
    print(f"Checks: {','.join(args.checks)}  Object modes: {','.join(args.object_modes)}  "
          f"Granularities: {','.join(args.object_granularities)}  "
          f"SVFG MSSA: {args.svfg_mssa}  Saber SMT: {'yes' if args.saber_smt else 'no'}")
    print(f"Timeout: {args.timeout:g}s  RSS limit: {args.mem_limit_gb:g} GiB")
    print(f"Context: {context_mode}; context limit: "
          f"{context_limit if context_limit is not None else 'unlimited'}")
    print("IR search budgets: " + ", ".join(f"{k}={v or 'unlimited'}" for k, v in budgets.items()))
    if "saber" in tools:
        print("Saber remains a distinct system baseline; common k aligns only "
              "the call-string bound, not other analysis semantics.")
    print(f"Results: {args.results}")
    if args.max_workers > 1:
        print("Parallel runs may contend for CPU and memory; compare timings with care.")
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.max_workers) as executor:
        futures = {
            executor.submit(execute_task, index, task, args.timeout,
                            args.mem_limit_gb, binaries,
                            context_limit, budgets, context_mode, saber_budgets): index
            for index, task in enumerate(tasks)
        }
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            record = future.result()
            records[index] = record
            save_results(args.results, records)
            print(
                f"[{record['status']:12s}] {record['tool']:12s} "
                f"{record['bug_type']:16s} {record['bc']:16s} "
                f"mode={record['smt_mode']}/{record['object_mode'] or '-'}/"
                f"{record['object_granularity'] or '-':8s} "
                f"time={record['wall_time_s']:8.3f}s "
                f"rss={record['max_rss_mb']:8.1f}MiB "
                f"result={record.get('check_result') or '-':8s} "
                f"findings={record.get('findings')} "
                f"analysis={record.get('analysis_ms')}ms",
                flush=True,
            )
    print(f"Done. Results saved to {args.results}")
    return 0 if all(record["status"] == "SUCCESS" for record in records) else 1


if __name__ == "__main__":
    sys.exit(main())
