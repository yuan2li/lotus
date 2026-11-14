"""Shared experiment identity and backward-compatible result loading."""
import argparse
import json
from pathlib import Path

IR_BUDGETS = ("max-product-states", "max-summary-pairs")
SABER_BUDGETS = ("max-forward-items", "solver-timeout-ms")
STATUS_LABELS = {"SUCCESS": "OK", "TIMEOUT": "TO", "OOM": "OOM",
                 "INCOMPLETE": "INC", "PARSE_ERROR": "PE", "SPEC_ERROR": "SPEC"}


def budget_value(text):
    if text == "unlimited":
        return 0
    try:
        value = int(text)
        if value < 0:
            raise ValueError
        return value
    except ValueError:
        raise argparse.ArgumentTypeError("expected a nonnegative integer or unlimited")


def canonical_limits(tool, limits=None):
    values = {} if tool == "saber" else dict.fromkeys(IR_BUDGETS, 0)
    values.update(limits or {})
    values = {key: 0 if value == "unlimited" else int(value) for key, value in values.items()}
    if any(value < 0 for value in values.values()):
        raise ValueError("search budgets must be nonnegative")
    return json.dumps(values, sort_keys=True, separators=(",", ":"))


def feasibility_enabled(record, metadata=None):
    if record["tool"] != "usetracessa":
        return False
    if "path_feasibility" in record:
        if not isinstance(record["path_feasibility"], bool):
            raise ValueError("path_feasibility must be a JSON boolean")
        return record["path_feasibility"]
    arguments = record.get("command")
    if not arguments:
        if "path_feasibility" in (metadata or {}):
            return feasibility_enabled(dict(record, path_feasibility=metadata["path_feasibility"]))
        arguments = (metadata or {}).get("argv", [])
    for argument in arguments:
        if argument in ("--path-feasibility", "--path-feasibility=true", "--path-feasibility=1"):
            return True
        if argument in ("--path-feasibility=false", "--path-feasibility=0"):
            return False
    return record.get("feasibility_witnesses") is not None


def load_results(paths):
    paths = list(paths)
    records = []
    identities = []
    for path in paths:
        data = json.loads(Path(path).read_text())
        metadata = data.get("metadata", {}) if isinstance(data, dict) else {}
        source = data["records"] if isinstance(data, dict) else data
        file_commit = metadata.get("lotus_commit")
        for original in source:
            record = dict(original)
            commit = record.get("lotus_commit", file_commit)
            if file_commit and commit and file_commit != commit:
                raise ValueError(f"conflicting record/file commit in {path}")
            record["lotus_commit"] = commit
            record["lotus_dirty"] = record.get("lotus_dirty", metadata.get("lotus_dirty"))
            record["path_feasibility"] = feasibility_enabled(record, metadata)
            if "search_limits" not in record:
                key = "saber_budgets" if record["tool"] == "saber" else "ir_budgets"
                record["search_limits"] = metadata.get("limits", {}).get(key, {})
            if not record.get("benchmark"):
                record["benchmark"] = Path(record["bc"]).stem
            record.setdefault("repeat", 0)
            identities.append((commit, record["lotus_dirty"]))
            records.append(record)
    commits = {commit for commit, _ in identities}
    if len(commits) > 1:
        raise ValueError("input commits differ or mix known/unknown provenance; summarize separately")
    if len({dirty for _, dirty in identities}) > 1:
        raise ValueError("input source-tree states differ; summarize separately")
    if len(paths) > 1 and (None in commits or any(dirty is not False for _, dirty in identities)):
        raise ValueError("merging result files requires one verified commit and clean source trees")
    return records


def add_analysis_options(parser):
    parser.add_argument("--path-feasibility", action="store_true",
                        help="Select UseTraceSSA runs with witness feasibility enabled")
    for flag in IR_BUDGETS:
        parser.add_argument("--" + flag, type=budget_value, default=0)
    for flag in SABER_BUDGETS:
        parser.add_argument("--saber-" + flag, type=budget_value, default=None)


def selected_limits(args, tool):
    if tool == "saber":
        return {key: value for key in SABER_BUDGETS
                if (value := getattr(args, "saber_" + key.replace("-", "_"))) is not None}
    return {key: getattr(args, key.replace("-", "_")) for key in IR_BUDGETS}
