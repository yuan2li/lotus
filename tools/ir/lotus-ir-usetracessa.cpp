#include "IR/ICFG/ICFGBuilder.h"
#include "IR/SVFG/SVFGBuilder.h"
#include "IR/UseTraceSSA/DefectDetector.h"
#include "IR/UseTraceSSA/FlowSensitivePointsTo.h"
#include "IR/UseTraceSSA/Query.h"
#include "IR/UseTraceSSA/ResultExport.h"
#include "IR/UseTraceSSA/SVFGBridge.h"
#include "IR/UseTraceSSA/TraceQueryOptions.h"

#include <llvm/IR/IntrinsicInst.h>
#include <llvm/IR/LLVMContext.h>
#include <llvm/IR/Module.h>
#include <llvm/IRReader/IRReader.h>
#include <llvm/Support/CommandLine.h>
#include <llvm/Support/InitLLVM.h>
#include <llvm/Support/SourceMgr.h>
#include <llvm/Support/raw_ostream.h>

#include <algorithm>
#include <chrono>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <stdexcept>

using namespace lotus::analysis;
using namespace lotus::usetracessa;

namespace {
llvm::cl::opt<std::string> Input(llvm::cl::Positional,
                                  llvm::cl::desc("<input.ll or input.bc>"),
                                  llvm::cl::Required);
llvm::cl::opt<std::string> Format("format", llvm::cl::desc("text, json, or dot"),
                                   llvm::cl::init("text"));
llvm::cl::opt<std::string> DumpSVFG("dump-svfg",
                                    llvm::cl::desc("Write the source SVFG DOT to a file"),
                                    llvm::cl::init(""));
llvm::cl::opt<std::string> Check("check",
                                 llvm::cl::desc(
                                     "double-free, use-after-free, memory-leak, or file-leak"),
                                 llvm::cl::init(""));
llvm::cl::opt<bool> Timing("timing",
                            llvm::cl::desc("Print analysis phase timings to stderr"));
llvm::cl::opt<std::string> SVFGMemorySSA(
    "svfg-mssa",
    llvm::cl::desc("SVFG Memory SSA: auto (off for --check), on, or off"),
    llvm::cl::init("auto"));
llvm::cl::opt<bool> HeapObjectsOnly(
    "heap-objects-only",
    llvm::cl::desc("Heap checks track heap objects only, dropping stack, global "
                   "and constant points-to objects (default: true; false keeps "
                   "every object, the earlier behavior)"),
    llvm::cl::init(true));
llvm::cl::opt<std::string> PointerAnalysis(
    "pta",
    llvm::cl::desc("Points-to sets of event pointers: andersen (the SVFG's) or "
                   "fspta (flow-sensitive over the same SVFG; builds Memory SSA)"),
    llvm::cl::init("andersen"));
llvm::cl::opt<std::string> SVFGMemoryPartition(
    "svfg-memory-partition",
    llvm::cl::desc("Memory SSA regions: distinct (default), intra-disjoint or "
                   "inter-disjoint (lotus-alias-fspta's default; faster for "
                   "--pta=fspta on some programs, but coarser)"),
    llvm::cl::init("distinct"));
llvm::cl::opt<bool> SVFGPointerOnly(
    "svfg-pointer-only",
    llvm::cl::desc("Keep only pointer-typed SVFG value flows (default: true)"),
    llvm::cl::init(true));
llvm::cl::opt<std::string> ObjectGranularity(
    "object-granularity",
    llvm::cl::desc("Resource objects: site (merge per allocation site) or "
                   "abstract (SVFG object IDs)"),
    llvm::cl::init("site"));
llvm::cl::opt<std::string> ObjectMode(
    "object-mode",
    llvm::cl::desc("--check search: batch (one mask-lifted scan) or fixed "
                   "(one same-object query per candidate)"),
    llvm::cl::init("batch"));
llvm::cl::opt<std::string> TemporalGraph(
    "temporal-graph",
    llvm::cl::desc("--check temporal graph: sparse (event/call skeleton) or "
                   "icfg (every instruction; the IDE-ICFG baseline)"),
    llvm::cl::init("sparse"));
llvm::cl::opt<std::string> ResultsJSON(
    "results-json",
    llvm::cl::desc("With --check, write per-object statuses and findings "
                   "deduplicated by sink site to this JSON file"),
    llvm::cl::init(""));
llvm::cl::opt<bool> ResultsWitnesses(
    "results-witnesses",
    llvm::cl::desc("Include one witness path with source locations per site "
                   "in --results-json"));
llvm::cl::opt<bool> Quiet("quiet",
                           llvm::cl::desc("Suppress issue and witness details"));
llvm::cl::opt<bool> FullLabels(
    "full-labels",
    llvm::cl::desc("Capture exact LLVM instruction labels even with --quiet"));
cli::QueryOptionsParser QueryOptionFlags;
llvm::cl::opt<unsigned> Source("source-node",
                                llvm::cl::desc("SVFG source node ID for a flow query"),
                                llvm::cl::init(std::numeric_limits<unsigned>::max()));
llvm::cl::opt<unsigned> Sink("sink-node",
                              llvm::cl::desc("SVFG sink node ID for a flow query"),
                              llvm::cl::init(std::numeric_limits<unsigned>::max()));

const char *statusName(QueryStatus status) {
  switch (status) {
  case QueryStatus::Found: return "Found";
  case QueryStatus::NotFound: return "NotFound";
  case QueryStatus::Unknown: return "Unknown";
  }
  return "Unknown";
}

std::string findingSite(const FlowNode &node) {
  const std::string &label = node.label;
  const auto functionEnd = label.find(".temporal:");
  const std::string function = label.substr(0, functionEnd);
  if (!node.sourceLocation.empty())
    return function + ':' + node.sourceLocation;
  // History versions differ between graph configurations; keep the site text.
  const auto space = label.find(' ');
  return space == std::string::npos ? label : function + ':' + label.substr(space + 1);
}
} // namespace

int main(int argc, char **argv) {
  llvm::InitLLVM init(argc, argv);
  llvm::cl::ParseCommandLineOptions(argc, argv, "Lotus SVFG use histories\n");
  cli::QueryOptions options;
  std::string optionError;
  if (!QueryOptionFlags.resolve(options, optionError)) {
    llvm::errs() << "lotus-ir-usetracessa: " << optionError << '\n';
    return 1;
  }
  SearchCompletion completion;
  bool query = Source != std::numeric_limits<unsigned>::max() ||
               Sink != std::numeric_limits<unsigned>::max();
  if ((Source == std::numeric_limits<unsigned>::max()) !=
      (Sink == std::numeric_limits<unsigned>::max())) {
    llvm::errs() << "lotus-ir-usetracessa: supply both --source-node and --sink-node\n";
    return 1;
  }
  if (PointerAnalysis != "andersen" && PointerAnalysis != "fspta") {
    llvm::errs() << "lotus-ir-usetracessa: --pta must be andersen or fspta\n";
    return 1;
  }
  if (SVFGMemorySSA != "auto" && SVFGMemorySSA != "on" && SVFGMemorySSA != "off") {
    llvm::errs() << "lotus-ir-usetracessa: --svfg-mssa must be auto, on, or off\n";
    return 1;
  }
  if (SVFGMemoryPartition != "distinct" &&
      SVFGMemoryPartition != "intra-disjoint" &&
      SVFGMemoryPartition != "inter-disjoint") {
    llvm::errs() << "lotus-ir-usetracessa: --svfg-memory-partition must be distinct, "
                    "intra-disjoint, or inter-disjoint\n";
    return 1;
  }
  if (ObjectGranularity != "site" && ObjectGranularity != "abstract") {
    llvm::errs() << "lotus-ir-usetracessa: --object-granularity must be site or abstract\n";
    return 1;
  }
  if (ObjectMode != "batch" && ObjectMode != "fixed") {
    llvm::errs() << "lotus-ir-usetracessa: --object-mode must be batch or fixed\n";
    return 1;
  }
  if (TemporalGraph != "sparse" && TemporalGraph != "icfg") {
    llvm::errs() << "lotus-ir-usetracessa: --temporal-graph must be sparse or icfg\n";
    return 1;
  }
  if (Format != "text" && Format != "json" && Format != "dot") {
    llvm::errs() << "lotus-ir-usetracessa: --format must be text, json, or dot\n";
    return 1;
  }
  if (query && Format != "text") {
    llvm::errs() << "lotus-ir-usetracessa: queries require --format=text\n";
    return 1;
  }
  if (!Check.empty() && query) {
    llvm::errs() << "lotus-ir-usetracessa: --check cannot be combined with a node query\n";
    return 1;
  }
  if (!Check.empty() && Format != "text") {
    llvm::errs() << "lotus-ir-usetracessa: --check requires --format=text\n";
    return 1;
  }
  if (!Check.empty() && Check != "double-free" && Check != "use-after-free" &&
      Check != "memory-leak" && Check != "file-leak") {
    llvm::errs() << "lotus-ir-usetracessa: unsupported --check rule\n";
    return 1;
  }

  using Clock = std::chrono::steady_clock;
  const auto start = Clock::now();
  llvm::LLVMContext context;
  llvm::SMDiagnostic diagnostic;
  auto module = llvm::parseIRFile(Input, diagnostic, context);
  if (!module) {
    diagnostic.print(argv[0], llvm::errs());
    return 1;
  }
  const auto parsed = Clock::now();
  auto millis = [](Clock::time_point from, Clock::time_point to) {
    return std::chrono::duration<double, std::milli>(to - from).count();
  };
  if (Timing)
    std::cerr << std::fixed << std::setprecision(3)
              << "timing_ms parse=" << millis(start, parsed) << '\n';
  try {
    ICFG icfg;
    ICFGBuilder icfgBuilder(&icfg);
    icfgBuilder.build(module.get());
    SVFGBuilderConfig config;
    config.usePointerAnalysis = true;
    config.buildMSSA = SVFGMemorySSA == "on" || PointerAnalysis == "fspta" ||
                       (SVFGMemorySSA == "auto" && Check.empty());
    config.pointerOnly = SVFGPointerOnly;
    // Disjoint regions merge objects that share an access, so --pta=fspta
    // solves fewer memory versions: sphinx3 drops from 36.5M node processes
    // to 0.47M, while milc slows down and some of its objects end Unknown.
    config.memoryPartition =
        SVFGMemoryPartition == "inter-disjoint"
            ? MemoryRegionPartitionStrategy::InterDisjoint
        : SVFGMemoryPartition == "intra-disjoint"
            ? MemoryRegionPartitionStrategy::IntraDisjoint
            : MemoryRegionPartitionStrategy::Distinct;
    config.resolveIndirectCalls = true;
    SVFGBuilder builder(config);
    std::unique_ptr<SVFG> svfg(builder.build(&icfg));
    const auto builtSVFG = Clock::now();
    if (Timing) std::cerr << "timing_ms svfg=" << millis(parsed, builtSVFG) << '\n';
    if (!DumpSVFG.empty()) svfg->dump(DumpSVFG);
    // Pointer-analysis ablation: flow-sensitive sets over the same SVFG.
    std::optional<lotus::usetracessa::cli::FlowSensitivePointsTo> flowSensitive;
    if (PointerAnalysis == "fspta") flowSensitive.emplace(*svfg, builder);
    const auto builtPTA = Clock::now();
    NativeHistoryMode mode = Check == "double-free" ? NativeHistoryMode::DoubleFree :
                             Check == "use-after-free" ? NativeHistoryMode::UseAfterFree :
                             Check == "memory-leak" ? NativeHistoryMode::MemoryLeak :
                             Check == "file-leak" ? NativeHistoryMode::FileLeak :
                                                          NativeHistoryMode::Full;
    NativeHistoryOptions historyOptions;
    historyOptions.instructionText = Check.empty() || !Quiet || FullLabels;
    historyOptions.allocationSiteObjects = ObjectGranularity == "site";
    historyOptions.heapObjectsOnly = HeapObjectsOnly;
    if (flowSensitive) historyOptions.pointsTo = std::cref(*flowSensitive);
    historyOptions.denseControlFlow = TemporalGraph == "icfg";
    if (Timing)
      historyOptions.phaseTiming = [](const char *phase, double elapsed) {
        std::cerr << "timing_ms " << phase << '=' << elapsed << '\n';
      };
    auto result =
        buildUseTraceSSAFromLotusSVFG(*svfg, *module, mode, historyOptions);
    const auto builtHistory = Clock::now();
    if (Timing)
      std::cerr << "timing_ms usetracessa=" << millis(builtPTA, builtHistory) << '\n';
    std::optional<DefectScan> checkReport;
    std::map<std::string, std::vector<std::size_t>> findingGroups;
    std::map<std::string, std::set<ObjectID>> findingObjects;
    std::optional<QueryResult> nodeQuery;
    if (!Check.empty()) {
      DefectKind kind = Check == "double-free" ? DefectKind::DoubleFree :
                        Check == "use-after-free" ? DefectKind::UseAfterFree :
                        Check == "memory-leak" ? DefectKind::MemoryLeak :
                                                 DefectKind::FileLeak;
      DefectScanOptions scanOptions{!Quiet, !Quiet || ResultsWitnesses, false,
                                    !Quiet || !ResultsJSON.empty()};
      scanOptions.fixedObjects = ObjectMode == "fixed";
      checkReport = DefectDetector(result.graph, options.depth, options.limits,
                                   options.mode)
                        .scan(kind, scanOptions);
      completion.merge(checkReport->completion);
      if (Timing)
        std::cerr << "timing_ms check_search="
                  << checkReport->searchMilliseconds
                  << " check_reporting=" << checkReport->reportingMilliseconds
                  << '\n';
      for (std::size_t index = 0; index < checkReport->findings.size(); ++index) {
        const auto &sink = result.graph.node(checkReport->findings[index].sink);
        std::string site = findingSite(sink);
        findingGroups[site].push_back(index);
        if (!Quiet) {
          const auto &objects = checkReport->findings[index].objects;
          findingObjects[site].insert(objects.begin(), objects.end());
        }
      }
    }
    if (query) {
      auto source = result.native.nodes.find(Source);
      auto sink = result.native.nodes.find(Sink);
      if (source == result.native.nodes.end() || sink == result.native.nodes.end())
        throw std::invalid_argument("source or sink SVFG node was not imported");
      Query request;
      request.sources = {source->second};
      request.sinks = {sink->second};
      options.apply(request);
      nodeQuery = QueryEngine(result.graph).run(request);
      completion.merge(nodeQuery->completion);
    }
    const auto analyzed = Clock::now();
    if (Format == "json") result.graph.printJSON(std::cout);
    else if (Format == "dot") result.graph.printDOT(std::cout);
    else {
      const auto stats = result.graph.statistics();
      std::cout << "svfg_nodes=" << svfg->getNumNodes()
                << " svfg_edges=" << svfg->getStat().numEdges
                << " context_limit=" << options.depthName()
                << " context_mode=" << options.contextName()
                << " history_nodes=" << stats.historyNodes
                << " history_psi=" << stats.historyPsiNodes
                << " history_phi=" << stats.historyPhiNodes
                << " flow_edges=" << stats.flowEdges
                << " guarded_effects=" << stats.guardedEffects
                << " known_object_cardinality=" << stats.knownObjectCardinality
                << " unknown_object_sets=" << stats.unknownObjectSets
                << " issues=" << result.graph.issues().size()
                << " excluded_nonheap_objects=" << result.excludedNonHeapObjects;
      // Alias multiplicity k of known guards at relevant sites (RQ2).
      auto guards = stats.effectGuardSizes;
      std::sort(guards.begin(), guards.end());
      std::size_t guardTotal = 0;
      std::vector<std::size_t> guardHistogram;
      for (auto k : guards) {
        guardTotal += k;
        unsigned bucket = 0;
        while ((std::size_t(2) << bucket) <= k) ++bucket;
        if (guardHistogram.size() <= bucket) guardHistogram.resize(bucket + 1);
        ++guardHistogram[bucket];
      }
      std::cout << " guard_k_count=" << guards.size() << " guard_k_mean="
                << (guards.empty() ? 0.0 : double(guardTotal) / guards.size())
                << " guard_k_median="
                << (guards.empty() ? 0 : guards[guards.size() / 2])
                << " guard_k_p90="
                << (guards.empty() ? 0 : guards[(9 * guards.size() + 9) / 10 - 1])
                << " guard_k_max=" << (guards.empty() ? 0 : guards.back())
                << " guard_k_hist=";
      for (std::size_t bucket = 0; bucket < guardHistogram.size(); ++bucket)
        std::cout << (bucket ? "," : "") << guardHistogram[bucket];
      std::size_t functions = 0, blocks = 0, instructions = 0;
      for (const auto &function : *module) {
        if (function.isDeclaration()) continue;
        ++functions;
        for (const auto &block : function) {
          ++blocks;
          for (const auto &instruction : block)
            instructions += !llvm::isa<llvm::DbgInfoIntrinsic>(instruction);
        }
      }
      std::cout << " icfg_nodes=" << icfg.getTotalNodeNum()
                << " icfg_edges=" << icfg.getTotalEdgeNum()
                << " ir_functions=" << functions << " ir_blocks=" << blocks
                << " ir_instructions=" << instructions << '\n';
      if (!Quiet)
        for (const auto &issue : result.graph.issues())
          std::cout << "issue: " << issue << '\n';
      if (checkReport) {
        const auto &scan = *checkReport;
        std::cout << "check=" << Check << " object_mode=" << ObjectMode
                  << " temporal_graph=" << TemporalGraph
                  << " pta=" << PointerAnalysis
                  << " result=" << statusName(scan.status)
                  << " findings=" << scan.findings.size()
                  << " finding_sites=" << findingGroups.size()
                  << " exhaustive=" << (scan.exhaustive ? "yes" : "no");
        cli::printCompletion(std::cout, scan.completion);
        std::cout << '\n';
        const auto &qs = scan.statistics;
        std::cout << "product_states=" << qs.productStates
                  << " product_edges=" << qs.productEdges
                  << " edges_examined=" << qs.edgesExamined
                  << " summary_pairs=" << qs.summaryPairs
                  << " mask_intersections=" << qs.maskIntersections
                  << " mask_unions=" << qs.maskUnions
                  << " nonempty_deltas=" << qs.nonemptyDeltas
                  << " candidate_objects=" << qs.candidateObjects
                  << " found_objects=" << qs.foundObjects
                  << " notfound_objects=" << qs.notFoundObjects
                  << " unknown_objects=" << qs.unknownObjects
                  << " pushes=" << qs.pushes
                  << " pushed_object_bits=" << qs.pushedObjectBits
                  << " sigma="
                  << (qs.pushes ? double(qs.pushedObjectBits) / qs.pushes : 0.0)
                  << " push_sizes=";
        for (std::size_t bucket = 0; bucket < qs.pushSizes.size(); ++bucket)
          std::cout << (bucket ? "," : "") << qs.pushSizes[bucket];
        std::cout << '\n';
        if (!Quiet && !scan.message.empty())
          std::cout << "message: " << scan.message << '\n';
        if (!Quiet)
          for (const auto &group : findingGroups) {
            std::cout << "finding_site=" << group.first
                      << " candidates=" << group.second.size()
                      << " objects=" << findingObjects.at(group.first).size() << '\n';
            for (auto id : scan.findings[group.second.front()].result.nodes) {
              const auto &node = result.graph.node(id);
              std::cout << "witness_node=" << id << " label=" << node.label;
              auto object = scan.findings[group.second.front()].witnessObject;
              if (object) std::cout << " object=" << *object;
              std::cout << '\n';
            }
          }
      }
      if (nodeQuery) {
        const auto &answer = *nodeQuery;
        std::cout << "query=" << statusName(answer.status)
                  << " witness_edges=" << answer.edges.size()
                  << " product_states=" << answer.productStates
                  << " edges_examined=" << answer.edgesExamined
                  << " summary_pairs=" << answer.summaryPairs;
        cli::printCompletion(std::cout, answer.completion);
        std::cout << '\n';
        if (!Quiet && !answer.message.empty())
          std::cout << "message: " << answer.message << '\n';
        if (!Quiet)
          for (auto node : answer.nodes)
            std::cout << "witness_node=" << node << '\n';
      }
    }
    if (checkReport && !ResultsJSON.empty()) {
      std::ofstream out(ResultsJSON);
      if (!out) throw std::runtime_error("cannot write " + ResultsJSON);
      std::function<std::vector<cli::WitnessStep>(std::size_t)> witnessOf;
      if (ResultsWitnesses)
        witnessOf = [&](std::size_t index) {
          const auto &finding = checkReport->findings[index];
          auto report = finding.result.nodes.empty()
                            ? checkReport->materialize(index, result.graph)
                            : finding;
          std::vector<cli::WitnessStep> steps;
          for (auto id : report.result.nodes) {
            const auto &node = result.graph.node(id);
            steps.emplace_back(node.label, node.sourceLocation);
          }
          return steps;
        };
      StableObjectKeys objectKeys(*svfg, *module);
      cli::writeResultsJSON(
          out, "usetracessa", Check,
          {{"object_mode", ObjectMode},
           {"temporal_graph", TemporalGraph},
           {"pta", PointerAnalysis},
           {"heap_objects_only", HeapObjectsOnly ? "true" : "false"},
           {"object_granularity", ObjectGranularity}},
          *checkReport,
          [&](std::size_t index) {
            return findingSite(result.graph.node(checkReport->findings[index].sink));
          },
          witnessOf, objectKeys,
          [&](ObjectID object) { return objectKeys.location(object); });
    }
    if (Timing) {
      std::cerr << std::fixed << std::setprecision(3)
                << "timing_ms parse=" << millis(start, parsed)
                << " svfg=" << millis(parsed, builtSVFG)
                << " pta=" << millis(builtSVFG, builtPTA)
                << " usetracessa=" << millis(builtPTA, builtHistory)
                << " build=" << millis(builtPTA, builtHistory)
                << " check=" << millis(builtHistory, analyzed)
                << " analysis=" << millis(builtPTA, analyzed)
                << " total=" << millis(start, analyzed) << '\n';
    }
    cli::explainIncomplete(std::cerr, completion);
  } catch (const std::exception &error) {
    llvm::errs() << "lotus-ir-usetracessa: " << error.what() << '\n';
    return 1;
  }
  return completion.searchComplete ? 0 : 2;
}
