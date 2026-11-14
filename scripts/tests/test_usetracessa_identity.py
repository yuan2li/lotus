"""Regressions for experiment identity and matching coverage."""
import argparse
import json
import os
import subprocess
from unittest import mock

from test_usetracessa_pipeline import PipelineTestCase, compare, summarize, load_script
import usetracessa_results as results

evaluate = load_script("evaluate_usetracessa")
validate = load_script("validate_usetracessa")


class IdentityTests(PipelineTestCase):
    def envelope(self, records=None, commit="a" * 40, dirty=False, **metadata):
        return self.write_json(dict(records=records or [self.record()],
                                    metadata=dict(lotus_commit=commit, lotus_dirty=dirty, **metadata)))

    def test_same_clean_commit_files_merge(self):
        paths = [self.envelope(), self.envelope([self.record(repeat=1, analysis_ms=3000)])]
        row = next(iter(summarize.aggregate(summarize.load(paths)).values()))
        self.assertEqual(row["runs"], 2)
        self.assertEqual(row["analysis_ms"], 2000)
        self.assertEqual(row["lotus_commit"], "a" * 40)

    def test_different_commits_are_rejected_before_writing_tables(self):
        paths = [self.envelope(), self.envelope(commit="b" * 40)]
        for loader in (summarize.load, compare.load_records):
            with self.assertRaisesRegex(ValueError, "commits differ"):
                loader(paths)
        with self.assertRaises(SystemExit) as error, mock.patch("sys.stderr"):
            summarize.main([*(str(p) for p in paths), "--out-dir", str(self.root / "out")])
        self.assertEqual(error.exception.code, 2)
        self.assertFalse((self.root / "out").exists())

    def test_unknown_or_dirty_cross_file_provenance_is_rejected(self):
        for second in (dict(commit=None), dict(dirty=True), dict(dirty=None)):
            with self.subTest(second=second), self.assertRaises(ValueError):
                summarize.load([self.envelope(), self.envelope(**second)])
        with self.assertRaises(ValueError):
            summarize.load([self.write_json([self.record()]), self.write_json([self.record()])])

    def test_single_legacy_or_dirty_file_remains_usable(self):
        for path in (self.write_json([self.record()]), self.envelope(dirty=True)):
            self.assertEqual(len(summarize.load([path])), 1)

    def test_record_and_file_commit_must_agree(self):
        with self.assertRaisesRegex(ValueError, "conflicting"):
            summarize.load([self.envelope([self.record(lotus_commit="b" * 40)])])

    def test_budget_and_feasibility_changes_never_become_repeats(self):
        base = self.record()
        rows = summarize.aggregate([base,
                self.record(command=["tool", "--path-feasibility"], analysis_ms=9000),
                self.record(search_limits={"max-product-states": 100}, analysis_ms=5000)])
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["runs"] == 1 for row in rows.values()))
        self.assertEqual(summarize.find(rows, "example", "use-after-free", "usetracessa")["analysis_ms"], 1000)

    def test_equivalent_budget_dictionary_orders_and_unlimited_normalize(self):
        rows = summarize.aggregate([
            self.record(search_limits={"max-product-states": 0, "max-summary-pairs": 0}),
            self.record(repeat=1, search_limits={"max-summary-pairs": "unlimited"})])
        self.assertEqual(len(rows), 1)
        self.assertEqual(next(iter(rows.values()))["runs"], 2)

    def test_legacy_feasibility_inference_and_metadata_budget_fallback(self):
        old = self.record(command=["tool", "--path-feasibility"])
        path = self.envelope([old], limits={"ir_budgets": {"max-summary-pairs": 7}})
        row = summarize.load([path])[0]
        self.assertTrue(row["path_feasibility"])
        self.assertEqual(row["search_limits"], {"max-summary-pairs": 7})
        self.assertFalse(results.feasibility_enabled(self.record(tool="ufg", command=["--path-feasibility"])))
        self.assertTrue(results.feasibility_enabled(self.record(feasibility_witnesses=0)))

    def test_metadata_feasibility_and_explicit_false_are_respected(self):
        row = results.load_results([self.envelope(path_feasibility=True)])[0]
        self.assertTrue(row["path_feasibility"])
        row = self.record(command=["tool", "--path-feasibility=false"], feasibility_witnesses=0)
        self.assertFalse(results.feasibility_enabled(row))

    def test_string_feasibility_value_is_rejected_instead_of_becoming_truthy(self):
        with self.assertRaisesRegex(ValueError, "JSON boolean"):
            results.feasibility_enabled(self.record(path_feasibility="false"))

    def test_main_table_selects_budget_and_feasibility_explicitly(self):
        records = [self.record(analysis_ms=1000),
                   self.record(path_feasibility=True, search_limits={"max-product-states": 100}, analysis_ms=9000)]
        path = self.envelope(records)
        out = self.root / "selected"
        self.assertEqual(self.quiet(summarize.main, [str(path), "--out-dir", str(out),
                         "--path-feasibility", "--max-product-states", "100"])[0], 0)
        self.assertEqual(self.csv_rows(out / "table2.csv")[0]["utssa_time_s"], "9.00")
        self.assertEqual(len(self.csv_rows(out / "runs.csv")), 2)
        selected = json.loads((out / "selection.json").read_text())
        self.assertTrue(selected["path_feasibility"])
        self.assertEqual(selected["lotus_commit"], "a" * 40)

    def test_evaluator_explicitly_records_feasibility_and_container_dirty_state(self):
        task = evaluate.make_tasks([self.root / "fixture.ll"], ("usetracessa",), ("double-free",))[0]
        execution = dict(status="SUCCESS", stdout="", stderr="", wall_time_s=1, max_rss_mb=1)
        parsed = dict(check_result="Found", findings=1, finding_sites=1, total_ms=10)
        with mock.patch.object(evaluate, "run_cmd_with_limits", return_value=execution), \
                mock.patch.object(evaluate, "parse_ir_output", return_value=parsed):
            record = evaluate.execute_task(0, task, 30, 2, {"usetracessa": self.root / "tool"},
                                           3, path_feasibility=True)
        self.assertTrue(record["path_feasibility"])
        args = argparse.Namespace(preset="smoke", manifest=None, repeat=1, path_feasibility=False)
        with mock.patch.object(evaluate, "command_output", return_value=None), \
                mock.patch.object(evaluate, "git_commit", return_value="a" * 40), \
                mock.patch.dict(os.environ, {"LOTUS_DIRTY": "true"}):
            self.assertTrue(evaluate.run_metadata(args, {}, {})["lotus_dirty"])

    def test_metadata_probe_timeout_does_not_block_evaluation(self):
        with mock.patch.object(evaluate.subprocess, "check_output",
                               side_effect=subprocess.TimeoutExpired("git", 10)):
            self.assertIsNone(evaluate.command_output(["git", "status"]))


class CoverageTests(PipelineTestCase):
    def matching(self, records, expected=0, **options):
        out = self.root / "matches.csv"
        code, output = self.quiet(compare.match, records, out, 0, 2027,
                                  granularity="abstract", **options)
        self.assertEqual(code, expected)
        return self.csv_rows(out), self.csv_rows(out.with_suffix(".coverage.csv")), output

    def test_zero_alert_tool_keeps_a_zero_column_and_prevents_false_all(self):
        records = [self.with_details(), self.with_details({"BugTypes": []}, tool="saber")]
        rows, coverage, output = self.matching(records)
        self.assertEqual(rows[0]["saber/nosmt"], "0")
        self.assertEqual(rows[0]["saber/nosmt_status"], "OK")
        self.assertEqual(coverage[0]["complete"], "True")
        self.assertIn("all=0", output)

    def test_failed_tool_is_na_not_zero(self):
        records = [self.with_details(), self.record(tool="saber", status="TIMEOUT", details=None)]
        rows, coverage, output = self.matching(records, expected=2)
        self.assertEqual(rows[0]["saber/nosmt"], "NA")
        self.assertEqual(rows[0]["saber/nosmt_status"], "TO")
        self.assertEqual(coverage[0]["complete"], "False")
        self.assertIn("all=NA", output)

    def test_no_alerts_anywhere_still_produce_coverage(self):
        records = [self.with_details({"objects": [], "findings": []}),
                   self.with_details({"BugTypes": []}, tool="saber")]
        rows, coverage, output = self.matching(records)
        self.assertEqual(rows, [])
        self.assertEqual(coverage[0]["saber/nosmt"], "0")
        self.assertEqual(coverage[0]["complete"], "True")
        self.assertIn("all=0", output)

    def test_expected_but_absent_tool_is_visible(self):
        rows, coverage, _ = self.matching([self.with_details()], expected=2,
                                         tools=("usetracessa", "ufg", "saber"))
        self.assertEqual(rows[0]["ufg_status"], "NA")
        self.assertEqual(rows[0]["saber/nosmt"], "NA")
        self.assertEqual(coverage[0]["complete"], "False")

    def test_missing_or_bad_details_are_parse_errors(self):
        for value in (None, "/absent/details.json", str(self.write_json({}))):
            with self.subTest(value=value):
                rows, _, _ = self.matching([self.with_details(), self.record(tool="saber", details=value)], expected=2)
                self.assertEqual(rows[0]["saber/nosmt_status"], "PE")

    def test_duplicate_runs_do_not_union_alerts(self):
        rows, coverage, _ = self.matching([self.with_details(), self.with_details(self.details(line=20))], expected=2)
        self.assertEqual(rows, [])
        self.assertEqual(coverage[0]["usetracessa/batch/sparse_status"], "DUP")

    def test_unlocatable_findings_are_not_claimed_as_zero_alerts(self):
        details = self.details()
        details["findings"][0]["site"] = "main:no debug location"
        _, coverage, _ = self.matching([self.with_details(details)], expected=2)
        self.assertEqual(coverage[0]["usetracessa/batch/sparse_status"], "PE")

    def test_saber_smt_column_is_not_required_for_uaf(self):
        reports = {"BugTypes": []}
        records = [self.with_details(reports, tool="saber", bug_type="double-free", smt_mode=mode)
                   for mode in ("smt", "nosmt")]
        records.append(self.with_details(reports, tool="saber"))
        _, coverage, _ = self.matching(records)
        uaf = next(row for row in coverage if row["client"] == "use-after-free")
        self.assertEqual(uaf["saber/smt_status"], "NA")
        self.assertEqual(uaf["complete"], "True")

    def test_partial_model_retains_positives_but_not_negative_claims(self):
        records = [self.with_details(model_complete=False),
                   self.with_details(self.details(line=20), tool="ufg")]
        rows, _, output = self.matching(records, expected=2)
        self.assertEqual(rows[0]["usetracessa/batch/sparse"], "1")
        self.assertEqual(rows[1]["usetracessa/batch/sparse"], "NA")
        self.assertIn("all=NA", output)

    def test_budget_and_feasibility_selection_prevents_alert_union(self):
        records = [self.with_details(self.details(line=10)),
                   self.with_details(self.details(line=20), path_feasibility=True),
                   self.with_details(self.details(line=30), search_limits={"max-product-states": 10})]
        for options, line in (({}, 10), ({"path_feasibility": True}, 20),
                              ({"budgets": {"max-product-states": 10}}, 30)):
            with self.subTest(options=options):
                rows, _, _ = self.matching(records, **options)
                self.assertEqual([row["location"] for row in rows], [f"test.c:{line}"])


class ValidationTests(PipelineTestCase):
    def test_failed_validation_step_propagates_error_with_log(self):
        with mock.patch.object(validate.subprocess, "run", return_value=mock.Mock(returncode=7)):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                self.quiet(validate.run_step, "bad", ["tool"], self.root)
        self.assertTrue((self.root / "bad.log").exists())

    def test_missing_tool_rejects_validation(self):
        with mock.patch("sys.stderr"):
            code = validate.main(["--bin-dir", str(self.root / "absent"), "--out-dir", str(self.root / "out")])
        self.assertEqual(code, 1)

    def validation_bundle(self, commit="a" * 40, coverage_status="UNK"):
        binaries = self.root / "bin"
        binaries.mkdir()
        for name in validate.TOOLS:
            tool = binaries / name
            tool.write_text("")
            tool.chmod(0o755)
        clients = ("double-free", "use-after-free", "memory-leak", "file-leak")
        calls = []

        def step(name, command, directory, **options):
            calls.append(name)
            if name == "evaluate":
                data = dict(metadata=dict(lotus_commit=commit), records=[
                    dict(bug_type=clients[i % 4], details="details.json") for i in range(116)])
                (directory / "runs.json").write_text(json.dumps(data))
            if name == "match":
                (directory / "matches.coverage.csv").write_text(
                    "client,saber/nosmt_status,ufg_status,usetracessa/batch/sparse_status\n" +
                    "".join(f"{c},OK,{coverage_status},{coverage_status}\n" for c in clients))
                (directory / "matches.csv").write_text("client,usetracessa/batch/sparse\n" +
                    "".join(f"{c},1\n" for c in clients))

        return binaries, step, calls

    def test_validation_rejects_mismatched_commit(self):
        binaries, step, calls = self.validation_bundle()
        with mock.patch.object(validate, "run_step", side_effect=step), mock.patch("sys.stderr"):
            code = validate.main(["--smoke-only", "--bin-dir", str(binaries),
                                  "--expected-commit", "b" * 40, "--out-dir", str(self.root / "out")])
        self.assertEqual(code, 1)
        self.assertNotIn("oracle", calls)

    def test_validation_preserves_expected_unknown_coverage_and_skips_missing_test_bins(self):
        binaries, step, calls = self.validation_bundle()
        with mock.patch.object(validate, "run_step", side_effect=step):
            code, _ = self.quiet(validate.main, ["--smoke-only", "--bin-dir", str(binaries),
                                  "--expected-commit", "a" * 40, "--out-dir", str(self.root / "out")])
        self.assertEqual(code, 0)
        self.assertNotIn("ir_tests", calls)
        self.assertIn("match", calls)

    def test_validation_rejects_matching_infrastructure_failure(self):
        binaries, step, _ = self.validation_bundle(coverage_status="PE")
        with mock.patch.object(validate, "run_step", side_effect=step), mock.patch("sys.stderr"):
            code = validate.main(["--smoke-only", "--bin-dir", str(binaries),
                                  "--out-dir", str(self.root / "out")])
        self.assertEqual(code, 1)
