"""Local regressions; no benchmark kits, tool binaries or server are needed.

Run: python3 -B -m unittest discover -s scripts/tests -p 'test_usetracessa*.py' -v
"""
import contextlib
import csv
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_script(name):
    path = Path(__file__).resolve().parents[1] / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compare = load_script("compare_usetracessa")
summarize = load_script("summarize_usetracessa")


class PipelineTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="utssa-tests-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.serial = 0

    def write_json(self, payload):
        self.serial += 1
        path = self.root / f"input-{self.serial}.json"
        path.write_text(json.dumps(payload))
        return path

    def record(self, tool="usetracessa", **changes):
        record = dict(benchmark="example", bc="example.bc", tool=tool,
                      bug_type="use-after-free", status="SUCCESS", repeat=0,
                      smt_mode="nosmt", object_mode="batch", temporal_graph="sparse",
                      object_granularity="abstract", svfg_mssa="auto", pta="andersen",
                      object_scope="heap", pass_calls=True, context_mode="sensitive",
                      context_limit=3, check_result="Found", model_complete=True,
                      analysis_ms=1000, candidate_objects=2, product_states=10,
                      pushes=5, pushed_object_bits=8, sigma=1.6, summary_pairs=3,
                      ir_instructions=100, history_nodes=10, flow_nodes=20,
                      max_rss_mb=12, findings=1, finding_sites=1,
                      push_sizes=[2, 3], guard_k_hist=[1, 2])
        if tool != "usetracessa":
            record.update(object_mode=None, temporal_graph=None)
        if tool == "saber":
            record.update(object_granularity=None, svfg_mssa=None, pta=None,
                          object_scope=None, pass_calls=None)
        record.update(changes)
        return record

    def details(self, ids=(1, 2), keys=("A", "B"), statuses=("Found", "NotFound"),
                finding_object=1, line=10):
        objects = [dict(object=obj, key=key, status=status)
                   for obj, key, status in zip(ids, keys, statuses)]
        findings = [] if finding_object is None else [
            dict(site=f"main:src/test.c:{line}:1", objects=[finding_object],
                 object_keys=[next(o["key"] for o in objects if o["object"] == finding_object)])]
        return dict(objects=objects, findings=findings)

    def with_details(self, details=None, **changes):
        payload = self.details() if details is None else details
        return self.record(details=str(self.write_json(payload)), **changes)

    def quiet(self, function, *args, **kwargs):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = function(*args, **kwargs)
        return result, output.getvalue()

    def csv_rows(self, path):
        with Path(path).open(newline="") as source:
            return list(csv.DictReader(source))


class OracleTests(PipelineTestCase):
    def oracle(self, expected=None, actual=None, **changes):
        records = [self.with_details(expected),
                   self.with_details(actual, temporal_graph="svfg", **changes)]
        return self.quiet(compare.oracle, records)

    def test_same_id_set_with_permuted_object_assignments(self):
        actual = self.details(keys=("B", "A"), statuses=("NotFound", "Found"), finding_object=2)
        result, output = self.oracle(actual=actual)
        self.assertEqual(result, 0)
        self.assertIn("mismatches: 0", output)
        self.assertIn("comparisons: 1", output)

    def test_different_id_sets_use_stable_keys(self):
        result, _ = self.oracle(actual=self.details(ids=(8, 9), finding_object=8))
        self.assertEqual(result, 0)

    def test_stable_key_change_is_detected_even_with_same_ids_and_statuses(self):
        result, _ = self.oracle(actual=self.details(keys=("C", "B")))
        self.assertEqual(result, 1)

    def test_finding_key_change_is_detected(self):
        result, _ = self.oracle(actual=self.details(finding_object=2))
        self.assertEqual(result, 1)

    def test_key_multiplicity_is_preserved(self):
        expected = self.details(keys=("A", "A"), statuses=("Found", "Found"))
        actual = self.details(ids=(7,), keys=("A",), statuses=("Found",), finding_object=7)
        self.assertEqual(self.oracle(expected, actual)[0], 1)

    def test_keys_are_recovered_when_finding_object_keys_are_absent(self):
        expected, actual = self.details(), self.details(ids=(7, 8), finding_object=7)
        del expected["findings"][0]["object_keys"]
        del actual["findings"][0]["object_keys"]
        self.assertEqual(self.oracle(expected, actual)[0], 0)

    def test_missing_reference_is_unverified(self):
        record = self.with_details(temporal_graph="svfg")
        self.assertEqual(self.quiet(compare.oracle, [record])[0], 2)

    def test_reference_without_comparison_is_unverified(self):
        self.assertEqual(self.quiet(compare.oracle, [self.with_details()])[0], 2)

    def test_empty_input_is_unverified(self):
        self.assertEqual(self.quiet(compare.oracle, [])[0], 2)

    def test_empty_universes_are_valid_when_two_runs_are_compared(self):
        empty = dict(objects=[], findings=[])
        result, output = self.oracle(empty, empty)
        self.assertEqual(result, 0)
        self.assertIn("configuration): 0; comparisons: 1", output)

    def test_cli_loader_preserves_failed_runs(self):
        records = [self.with_details(), self.with_details(temporal_graph="svfg"),
                   self.record(tool="ufg", status="TIMEOUT", details=None)]
        path = self.write_json(dict(records=records))
        result, output = self.quiet(compare.main, ["oracle", str(path)])
        self.assertEqual(result, 2)
        self.assertIn("TIMEOUT", output)
        self.assertIn("comparisons: 1", output)

    def test_missing_details_is_unverified(self):
        records = [self.with_details(), self.record(temporal_graph="svfg", details=None)]
        self.assertEqual(self.quiet(compare.oracle, records)[0], 2)

    def test_unreadable_details_is_unverified(self):
        records = [self.with_details(), self.record(temporal_graph="svfg", details="/absent/detail.json")]
        self.assertEqual(self.quiet(compare.oracle, records)[0], 2)

    def test_malformed_reference_details_are_unverified(self):
        records = [self.with_details({"objects": []}), self.with_details(temporal_graph="svfg")]
        self.assertEqual(self.quiet(compare.oracle, records)[0], 2)

    def test_duplicate_reference_is_unverified(self):
        records = [self.with_details(), self.with_details(), self.with_details(temporal_graph="svfg")]
        self.assertEqual(self.quiet(compare.oracle, records)[0], 2)

    def test_legacy_abstract_without_keys_is_unverified(self):
        details = self.details()
        for obj in details["objects"]:
            del obj["key"]
        self.assertEqual(self.oracle(details, details)[0], 2)

    def test_legacy_site_ids_still_compare(self):
        details = self.details()
        for obj in details["objects"]:
            del obj["key"]
        records = [self.with_details(details, object_granularity="site"),
                   self.with_details(details, object_granularity="site", tool="ufg")]
        self.assertEqual(self.quiet(compare.oracle, records)[0], 0)

    def test_call_passing_mssa_and_contexts_do_not_share_references(self):
        for changes in (dict(pass_calls=False), dict(svfg_mssa="on"),
                        dict(context_limit=None), dict(context_mode="insensitive")):
            with self.subTest(changes=changes):
                self.assertEqual(self.oracle(**changes)[0], 2)


class MatchTests(PipelineTestCase):
    def matching(self, records, **options):
        out = self.root / "matches.csv"
        expected_status = options.pop("expected_status", 0)
        result, _ = self.quiet(compare.match, records, out, 0, 2027,
                               granularity="abstract", **options)
        self.assertEqual(result, expected_status)
        return self.csv_rows(out)

    def test_call_passing_settings_are_never_unioned(self):
        records = [self.with_details(self.details(line=10)),
                   self.with_details(self.details(line=20), pass_calls=False)]
        self.assertEqual([r["location"] for r in self.matching(records)], ["src/test.c:10"])
        self.assertEqual([r["location"] for r in self.matching(records, pass_calls=False)], ["src/test.c:20"])

    def test_graph_and_mssa_selection(self):
        records = [self.with_details(self.details(line=10)),
                   self.with_details(self.details(line=20), temporal_graph="svfg"),
                   self.with_details(self.details(line=30), svfg_mssa="on")]
        for options, line in [(dict(), 10), (dict(temporal_graph="svfg"), 20),
                              (dict(svfg_mssa="on"), 30)]:
            with self.subTest(options=options):
                self.assertEqual([r["location"] for r in self.matching(records, **options)],
                                 [f"src/test.c:{line}"])

    def test_failed_runs_are_retained_and_other_contexts_are_excluded(self):
        records = [self.with_details(self.details(line=10)),
                   self.with_details(self.details(line=20), context_limit=None),
                   self.record(tool="saber", status="SPEC_ERROR", details=None)]
        rows = self.matching(records, expected_status=2)
        self.assertEqual([r["location"] for r in rows], ["src/test.c:10"])
        self.assertEqual(rows[0]["saber/nosmt_status"], "SPEC")

    def test_legacy_call_passing_default_and_saber_are_retained(self):
        legacy = self.with_details()
        del legacy["pass_calls"]
        report = {"BugTypes": [{"Reports": [{"DiagSteps": [{"File": "src/test.c", "Line": 10}]}]}]}
        saber = self.with_details(report, tool="saber")
        row = self.matching([legacy, saber])[0]
        self.assertEqual(row["usetracessa/batch/sparse"], "1")
        self.assertEqual(row["saber/nosmt"], "1")

    def test_cli_passes_selection_and_sampling_is_reproducible(self):
        details = self.details()
        details["findings"] = [self.details(line=line)["findings"][0] for line in (10, 20, 30)]
        records = [self.with_details(details, pass_calls=False, temporal_graph="svfg")]
        path = self.write_json(records)
        out = self.root / "matched.csv"
        args = ["match", str(path), "--out", str(out), "--granularity", "abstract",
                "--pass-calls", "false", "--temporal-graph", "svfg", "--sample", "2",
                "--tools", "usetracessa"]
        self.assertEqual(self.quiet(compare.main, args)[0], 0)
        first = out.read_text()
        self.assertEqual(sum(int(r["sampled"]) for r in self.csv_rows(out)), 2)
        self.assertEqual(self.quiet(compare.main, args)[0], 0)
        self.assertEqual(out.read_text(), first)


class SummaryTests(PipelineTestCase):
    def summary(self, records, *options):
        source = self.write_json(dict(records=records))
        out = self.root / f"summary-{self.serial}"
        result, _ = self.quiet(summarize.main, [str(source), "--out-dir", str(out), *options])
        self.assertEqual(result, 0)
        return out

    def matrix(self):
        return [self.record(temporal_graph=graph, object_mode=mode, pass_calls=passing,
                            analysis_ms=1000 + 100 * i + 10 * j + k,
                            pushes=100 * i + 10 * j + k + 1)
                for i, graph in enumerate(("sparse", "svfg", "icfg"))
                for j, mode in enumerate(("batch", "fixed"))
                for k, passing in enumerate((True, False))]

    def test_table5_covers_two_by_two_and_call_passing_separately(self):
        records = self.matrix()
        out = self.summary(records)
        rows = self.csv_rows(out / "table5.csv")
        self.assertEqual(len(rows), 4)
        self.assertEqual({(r["temporal_graph"], r["pass_calls"]) for r in rows},
                         {(g, str(p)) for g in ("sparse", "svfg") for p in (True, False)})
        for row in rows:
            for mode in ("batch", "fixed"):
                source = next(r for r in records if r["temporal_graph"] == row["temporal_graph"]
                              and str(r["pass_calls"]) == row["pass_calls"] and r["object_mode"] == mode)
                self.assertEqual(row[mode + "_pushes"], str(source["pushes"]))
                self.assertEqual(row[mode + "_status"], "OK")
            self.assertEqual(row["icfg_status"], "OK")
        self.assertEqual(len((out / "table5.tex").read_text().splitlines()), 4)

    def test_main_graph_call_passing_and_d5_granularity(self):
        records = self.matrix() + [self.record(object_granularity="site", analysis_ms=99000),
                                   self.record(tool="ufg"), self.record(tool="saber")]
        out = self.summary(records, "--temporal-graph", "svfg", "--pass-calls", "false")
        row = self.csv_rows(out / "table2.csv")[0]
        self.assertEqual(row["utssa_time_s"], "1.10")
        self.assertEqual(row["temporal_graph"], "svfg")
        self.assertEqual(row["object_granularity"], "abstract")
        self.assertEqual(row["pass_calls"], "False")
        self.assertEqual(row["ufg_status"], "NA")
        self.assertEqual(row["saber_status"], "OK")
        table4 = self.csv_rows(out / "table4.csv")[0]
        self.assertEqual(table4["utssa_nodes"], "20")
        self.assertEqual(table4["d_temporal"], "5.0")

    def test_uaf_site_is_explicit_supplement_not_silent_fallback(self):
        records = [self.record(object_granularity="site")]
        out = self.summary(records)
        self.assertEqual(self.csv_rows(out / "table2.csv")[0]["utssa_status"], "NA")
        out = self.summary(records, "--object-granularity", "site")
        self.assertEqual(self.csv_rows(out / "table2.csv")[0]["utssa_status"], "OK")

    def test_other_clients_default_to_site_and_saber_nosmt(self):
        for bug in ("double-free", "memory-leak", "file-leak"):
            with self.subTest(bug=bug):
                records = [self.record(bug_type=bug, object_granularity="site"),
                           self.record(bug_type=bug, object_granularity="abstract", analysis_ms=9000),
                           self.record(tool="saber", bug_type=bug),
                           self.record(tool="saber", bug_type=bug, smt_mode="smt", analysis_ms=9000)]
                row = self.csv_rows(self.summary(records) / "table2.csv")[0]
                self.assertEqual(row["utssa_time_s"], "1.00")
                self.assertEqual(row["saber_time_s"], "1.00")
                self.assertEqual(row["object_granularity"], "site")

    def test_repeats_use_medians_and_failures_are_not_averaged(self):
        records = [self.record(analysis_ms=t, repeat=i) for i, t in enumerate((3000, 1000, 2000))]
        out = self.summary(records)
        self.assertEqual(self.csv_rows(out / "table2.csv")[0]["utssa_time_s"], "2.00")
        self.assertEqual(self.csv_rows(out / "runs.csv")[0]["runs"], "3")
        records[1]["status"] = "TIMEOUT"
        out = self.summary(records)
        self.assertEqual(self.csv_rows(out / "table2.csv")[0]["utssa_time_s"], "TO")
        self.assertEqual(self.csv_rows(out / "runs.csv")[0]["analysis_ms"], "")

    def test_failure_labels_survive_csv_tex_and_table4(self):
        for failure, label in (("TIMEOUT", "TO"), ("OOM", "OOM"), ("INCOMPLETE", "INC"),
                               ("PARSE_ERROR", "PE"), ("SPEC_ERROR", "SPEC"), ("ERROR(rc=1)", "ERR")):
            with self.subTest(failure=failure):
                out = self.summary([self.record(status=failure)])
                self.assertEqual(self.csv_rows(out / "table2.csv")[0]["utssa_time_s"], label)
                self.assertEqual(self.csv_rows(out / "table4.csv")[0]["d_temporal"], label)
                self.assertIn(label, (out / "table5.tex").read_text())

    def test_incomplete_model_is_unknown_in_paper_and_raw_metrics_are_preserved(self):
        out = self.summary([self.record(check_result="Unknown", model_complete=False, findings=0)])
        raw = self.csv_rows(out / "runs.csv")[0]
        self.assertEqual(raw["unknown_incomplete_model"], "True")
        self.assertEqual(raw["findings"], "0")
        self.assertEqual(raw["analysis_ms"], "1000")
        self.assertEqual(raw["status"], "UNK")
        for name, column in (("table2.csv", "utssa_time_s"), ("table4.csv", "d_alias_states"),
                             ("table5.csv", "batch_time_s"), ("rq1_time.csv", "utssa")):
            self.assertEqual(self.csv_rows(out / name)[0][column], "UNK")
        self.assertIn("UNK", (out / "table2.tex").read_text())

    def test_explicit_unknown_flag_in_any_repeat_taints_paper_cells(self):
        out = self.summary([self.record(repeat=0), self.record(repeat=1, unknown_incomplete_model=True)])
        self.assertEqual(self.csv_rows(out / "table2.csv")[0]["utssa_status"], "UNK")

    def test_histograms_retain_all_matrix_dimensions(self):
        out = self.summary(self.matrix())
        for name in ("push_sizes.csv", "guard_k.csv"):
            rows = self.csv_rows(out / name)
            configs = {(r["temporal_graph"], r["object_mode"], r["pass_calls"]) for r in rows}
            self.assertEqual(len(configs), 8)
            self.assertTrue(all(r["object_granularity"] == "abstract" for r in rows))

    def test_contexts_are_not_aggregated_or_selected_together(self):
        records = [self.record(analysis_ms=1000), self.record(context_limit=None, analysis_ms=9000)]
        out = self.summary(records, "--context-limit", "unlimited")
        self.assertEqual(len(self.csv_rows(out / "runs.csv")), 2)
        self.assertEqual(self.csv_rows(out / "table2.csv")[0]["utssa_time_s"], "9.00")

    def test_legacy_missing_or_null_defaults_merge_with_explicit_defaults(self):
        legacy = self.record(bug_type="double-free", object_granularity="site", analysis_ms=3000)
        for name in ("pta", "object_scope", "context_mode", "context_limit"):
            del legacy[name]
        for name in ("pass_calls", "svfg_mssa", "object_granularity"):
            legacy[name] = None
        current = self.record(bug_type="double-free", object_granularity="site", analysis_ms=1000, repeat=1)
        # Older files may be a bare list, not an envelope with records.
        source = self.write_json([legacy, current])
        out = self.root / "legacy-summary"
        self.assertEqual(self.quiet(summarize.main, [str(source), "--out-dir", str(out)])[0], 0)
        raw = self.csv_rows(out / "runs.csv")
        self.assertEqual(len(raw), 1)
        self.assertEqual(raw[0]["runs"], "2")
        self.assertEqual(self.csv_rows(out / "table2.csv")[0]["utssa_time_s"], "2.00")

    def test_d_alias_uses_product_states_and_d_temporal_uses_history_for_sparse(self):
        out = self.summary([self.record(), self.record(tool="ufg", product_states=100)])
        row = self.csv_rows(out / "table4.csv")[0]
        self.assertEqual(row["d_alias_states"], "10.0")
        self.assertEqual(row["d_temporal"], "10.0")

    def test_zero_work_is_distinct_from_an_undefined_ratio(self):
        out = self.summary([self.record(), self.record(tool="ufg", product_states=0)])
        self.assertEqual(self.csv_rows(out / "table4.csv")[0]["d_alias_states"], "0.0")
        out = self.summary([self.record(history_nodes=0)])
        self.assertEqual(self.csv_rows(out / "table4.csv")[0]["d_temporal"], "NA")

    def test_missing_fixed_or_icfg_is_explicit(self):
        out = self.summary([self.record()])
        row = self.csv_rows(out / "table5.csv")[0]
        self.assertEqual(row["fixed_pushes"], "NA")
        self.assertEqual(row["fixed_status"], "NA")
        self.assertEqual(row["icfg_status"], "NA")

    def test_selection_metadata_and_csv_column_counts(self):
        records = self.matrix() + [self.record(pta="fspta"), self.record(object_scope="all")]
        out = self.summary(records, "--temporal-graph", "svfg")
        selection = json.loads((out / "selection.json").read_text())
        self.assertEqual(selection["temporal_graph"], "svfg")
        self.assertEqual(selection["auto_granularity"]["use-after-free"], "abstract")
        for path in out.glob("*.csv"):
            for row in self.csv_rows(path):
                self.assertNotIn(None, row, path.name)
                self.assertTrue(all(value is not None for value in row.values()), path.name)

    def test_sensitivity_outputs_use_requested_graph_and_keep_status(self):
        records = [self.record(temporal_graph=graph, pta=pta, object_scope=scope)
                   for graph in ("sparse", "svfg") for pta in ("andersen", "fspta")
                   for scope in ("heap", "all")]
        out = self.summary(records, "--temporal-graph", "svfg")
        for name in ("pta_ablation.csv", "object_scope.csv"):
            rows = self.csv_rows(out / name)
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r["temporal_graph"] == "svfg" and r["status"] == "OK" for r in rows))

    def test_ufg_sensitivity_does_not_claim_a_utssa_skeleton(self):
        records = [self.record(tool="ufg", pta=pta, object_scope=scope)
                   for pta in ("andersen", "fspta") for scope in ("heap", "all")]
        out = self.summary(records, "--temporal-graph", "svfg")
        for name in ("pta_ablation.csv", "object_scope.csv"):
            rows = self.csv_rows(out / name)
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(r["temporal_graph"] == "" for r in rows))


if __name__ == "__main__":
    unittest.main()
