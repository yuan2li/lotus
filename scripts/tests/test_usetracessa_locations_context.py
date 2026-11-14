"""Source identity and unbounded-context regressions through the result pipeline."""
import argparse
import json
from unittest import mock

from test_usetracessa_pipeline import PipelineTestCase, compare, summarize, load_script
from usetracessa_results import canonical_context_limit

evaluate = load_script("evaluate_usetracessa")


class SourceLocationTests(PipelineTestCase):
    def at(self, path, tool="usetracessa", **changes):
        details = self.details()
        details["findings"][0]["site"] = f"main:{path}:10:1"
        return self.with_details(details, tool=tool, **changes)

    def matching(self, records, expected=0, **options):
        out = self.root / "matches.csv"
        code, output = self.quiet(compare.match, records, out, 0, 2027,
                                  granularity="abstract", **options)
        self.assertEqual(code, expected, output)
        return self.csv_rows(out)

    def test_same_basename_in_different_directories_never_matches(self):
        rows = self.matching([self.at("libA/util.c"), self.at("libB/util.c", "ufg")])
        self.assertEqual([r["location"] for r in rows], ["libA/util.c:10", "libB/util.c:10"])
        self.assertEqual([(r["ufg"], r["usetracessa/batch/sparse"]) for r in rows],
                         [("0", "1"), ("1", "0")])

    def test_paths_normalize_without_consulting_local_filesystem(self):
        rows = self.matching([self.at("src/./sub/../util.c"), self.at("src/util.c", "ufg")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["location"], "src/util.c:10")
        self.assertEqual(rows[0]["ufg"], "1")

    def test_explicit_roots_align_tools_and_are_recorded(self):
        records = [self.at("/build/a/project/src/util.c"),
                   self.at("/build/b/project/src/util.c", "ufg")]
        self.assertEqual(len(self.matching(records)), 2)
        rows = self.matching(records, path_maps=[("/build/a/project", "."),
                                               ("/build/b/project", ".")])
        self.assertEqual([r["location"] for r in rows], ["src/util.c:10"])
        selected = json.loads((self.root / "matches.selection.json").read_text())
        self.assertEqual(len(selected["source_prefix_maps"]), 2)

    def test_prefix_mapping_respects_components_and_longest_prefix(self):
        maps = [("/build", "root"), ("/build/project", "src")]
        self.assertEqual(compare.file_line("/build/project/a.c", 7, maps), "src/a.c:7")
        self.assertEqual(compare.file_line("/builder/a.c", 7, maps), "/builder/a.c:7")
        self.assertEqual(compare.file_line("/build/project2/a.c", 7, maps), "root/project2/a.c:7")

    def test_windows_drive_and_separator_are_preserved(self):
        self.assertEqual(compare.location(r"main:C:\repo\src\util.c:10:2"),
                         "C:/repo/src/util.c:10")
        self.assertEqual(compare.location("C:/repo/src/util.c:10:2", function_prefix=False),
                         "C:/repo/src/util.c:10")
        self.assertEqual(compare.location("ns::main:src/util.c:10:2"), "src/util.c:10")
        self.assertEqual(compare.location("ns::main:C:/repo/util.c:10:2"), "C:/repo/util.c:10")

    def test_legacy_basename_is_ambiguous_against_qualified_paths(self):
        rows = self.matching([self.at("util.c"), self.at("libA/util.c", "ufg")], expected=2)
        qualified = next(r for r in rows if r["location"] == "libA/util.c:10")
        self.assertEqual(qualified["usetracessa/batch/sparse"], "NA")
        self.assertEqual(qualified["usetracessa/batch/sparse_status"], "AMB")
        coverage = self.csv_rows(self.root / "matches.coverage.csv")[0]
        self.assertEqual(coverage["complete"], "False")
        self.assertEqual(coverage["usetracessa/batch/sparse"], "NA")

    def test_two_legacy_basenames_cannot_claim_verified_match(self):
        records = [self.at("util.c"), self.at("util.c", "ufg")]
        rows = self.matching(records, expected=2)
        self.assertEqual(rows[0]["ufg_status"], "AMB")
        self.assertEqual(rows[0]["usetracessa/batch/sparse_status"], "AMB")
        rows = self.matching(records, path_maps=[("util.c", "src/util.c")])
        self.assertEqual(rows[0]["location"], "src/util.c:10")
        self.assertEqual(rows[0]["ufg_status"], "OK")

    def test_allocation_witness_and_saber_paths_use_same_normalization(self):
        record = self.record(bug_type="double-free")
        details = self.details()
        details["objects"][0]["location"] = "/root/libA/util.c:8"
        maps = [("/root", ".")]
        self.assertEqual(compare.ir_locations(record, details, maps), {"libA/util.c:8"})
        report = {"BugTypes": [{"Reports": [{"DiagSteps": [
            {"File": "/root/libA/util.c", "Line": 8},
            {"File": "/root/libB/util.c", "Line": 12}]}]}]}
        self.assertEqual(compare.saber_locations(record, report, maps), {"libA/util.c:8"})
        self.assertEqual(compare.saber_locations(self.record(), report, maps), {"libB/util.c:12"})
        details["objects"][0].pop("location")
        details["findings"][0]["witness"] = [{"location": "/root/libA/util.c:8:2"}]
        self.assertEqual(compare.ir_locations(self.record(bug_type="memory-leak"), details, maps),
                         {"libA/util.c:8"})

    def test_mapping_cli_and_invalid_or_duplicate_maps(self):
        records = [self.at("/a/src/util.c"), self.at("/b/src/util.c", "ufg")]
        path = self.write_json(records)
        out = self.root / "cli.csv"
        command = ["match", str(path), "--out", str(out), "--tools", "usetracessa,ufg",
                   "--granularity", "abstract", "--source-prefix-map", "/a=.",
                   "--source-prefix-map", "/b=."]
        self.assertEqual(self.quiet(compare.main, command)[0], 0)
        self.assertEqual(self.csv_rows(out)[0]["location"], "src/util.c:10")
        for suffix in (["--source-prefix-map", "bad"],
                       ["--source-prefix-map", "/a=other"]):
            with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
                compare.main(command + suffix)


class ContextIdentityTests(PipelineTestCase):
    def test_historical_spellings_merge_but_zero_and_default_stay_separate(self):
        records = [self.record(context_limit=value, repeat=index)
                   for index, value in enumerate((None, "unlimited", "unbounded", 0, "0", 3, "3"))]
        rows = summarize.aggregate(records)
        self.assertEqual(len(rows), 3)
        for limit, count in ((None, 3), (0, 2), (3, 2)):
            row = summarize.find(rows, "example", "use-after-free", "usetracessa",
                                 context_limit=limit)
            self.assertEqual(row["runs"], count)
        self.assertEqual(summarize.find(rows, "example", "use-after-free", "usetracessa",
                                       context_limit="unlimited")["runs"], 3)

    def test_loader_normalizes_legacy_and_enveloped_json(self):
        for data in ([self.record(context_limit="unlimited")],
                     {"records": [self.record(context_limit="unbounded")], "metadata": {}}):
            for loader in (summarize.load, compare.load_records):
                self.assertIsNone(loader([self.write_json(data)])[0]["context_limit"])
        legacy = self.record()
        legacy.pop("context_limit")
        self.assertEqual(summarize.load([self.write_json([legacy])])[0]["context_limit"], 3)

    def test_invalid_context_values_fail_before_outputs(self):
        for value in (-1, "-1", True, False, 3.0, "3.0", "", "infinity", [], {}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    canonical_context_limit(value)
                with self.assertRaises(ValueError):
                    summarize.load([self.write_json([self.record(context_limit=value)])])

    def test_evaluator_cli_preserves_zero_and_validates_tool_range(self):
        self.assertEqual(evaluate.context_limit_value("0"), 0)
        self.assertIsNone(evaluate.context_limit_value("unlimited"))
        for value in ("-1", "3.0", str(2**64)):
            with self.assertRaises(argparse.ArgumentTypeError):
                evaluate.context_limit_value(value)

    def test_evaluator_keeps_authoritative_configuration_over_stdout(self):
        task = evaluate.make_tasks([self.root / "fixture.ll"], ("usetracessa",), ("double-free",))[0]
        execution = dict(status="SUCCESS", stdout="", stderr="", wall_time_s=1, max_rss_mb=1)
        parsed = dict(check_result="Found", findings=1, finding_sites=1, total_ms=10,
                      context_limit="unlimited", context_mode="insensitive", status="BAD")
        with mock.patch.object(evaluate, "run_cmd_with_limits", return_value=execution), \
                mock.patch.object(evaluate, "parse_ir_output", return_value=parsed):
            for limit in (None, 0, 3):
                record = evaluate.execute_task(0, task, 30, 2, {"usetracessa": self.root / "tool"}, limit)
                self.assertEqual(record["context_limit"], limit)
                self.assertEqual(record["context_mode"], "sensitive")
                self.assertEqual(record["status"], "SUCCESS")
                self.assertEqual(record["findings"], 1)

    def test_unbounded_legacy_runs_populate_main_tables_and_match(self):
        records = [self.with_details(context_limit="unlimited"),
                   self.with_details(tool="ufg", context_limit=None)]
        path = self.write_json(records)
        out = self.root / "tables"
        self.assertEqual(self.quiet(summarize.main, [str(path), "--out-dir", str(out),
                                                    "--context-limit", "unlimited"])[0], 0)
        row = self.csv_rows(out / "table2.csv")[0]
        self.assertEqual(row["utssa_time_s"], "1.00")
        self.assertEqual(row["ufg_time_s"], "1.00")
        self.assertIsNone(json.loads((out / "selection.json").read_text())["context_limit"])
        self.assertEqual(self.quiet(compare.match, records, self.root / "matches.csv", 0, 1,
                                    granularity="abstract", context_limit="unlimited")[0], 0)

    def test_oracle_groups_null_and_unlimited_together(self):
        code, output = self.quiet(compare.oracle, [self.with_details(context_limit=None),
                    self.with_details(tool="ufg", context_limit="unlimited")])
        self.assertEqual(code, 0, output)
        self.assertIn("comparisons: 1; mismatches: 0; unverified: 0", output)
