#include "IR/ICFG/ICFGBuilder.h"
#include "IR/SVFG/SVFGBuilder.h"
#include "IR/UFG/DefectDetector.h"
#include "IR/UseTraceSSA/ResultExport.h"
#include "IR/UseTraceSSA/FlowSensitivePointsTo.h"
#include "IR/UseTraceSSA/SVFGBridge.h"
#include "IR/UseTraceSSA/TraceQueryOptions.h"

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
llvm::cl::opt<bool> PassCalls(
    "pass-calls",
    llvm::cl::desc("--check: objects that no event of a call's targets concerns "
                   "pass over the call (default); false sends every object "
                   "through every callee"),
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
llvm::cl::opt<std::string> ResultsJSON(
    "results-json",
    llvm::cl::desc("With --check, write per-object statuses and findings "
                   "deduplicated by sink site to this JSON file"),
    llvm::cl::init(""));
llvm::cl::opt<bool> ResultsWitnesses(
    "results-witnesses",
    llvm::cl::desc("Include one witness path with source locations per site "
                   "in --results-json"));
llvm::cl::opt<bool> FullLabels(
    "full-labels",
    llvm::cl::desc("Capture exact LLVM instruction labels even with --quiet"));
llvm::cl::opt<bool> Quiet("quiet",
                           llvm::cl::desc("Suppress issue and witness details"));
cli::QueryOptionsParser QueryOptionFlags;
llvm::cl::opt<unsigned> Source("source-node",
                                llvm::cl::desc("SVFG source node ID for a flow query"),
                                llvm::cl::init(std::numeric_limits<unsigned>::max()));
llvm::cl::opt<unsigned> Sink("sink-node",
                              llvm::cl::desc("SVFG sink node ID for a flow query"),
                              llvm::cl::init(std::numeric_limits<unsigned>::max()));
llvm::cl::opt<std::uint64_t> Object("object",
                                    llvm::cl::desc("Object ID for a UFG node query"),
                                    llvm::cl::init(UnknownResource));

const char *statusName(QueryStatus status) {
  switch (status) {
  case QueryStatus::Found: return "Found";
  case QueryStatus::NotFound: return "NotFound";
  case QueryStatus::Unknown: return "Unknown";
  }
  return "Unknown";
}

std::string findingSite(const FlowNode &node) {
  const auto objectSuffix = node.label.rfind(" @object:");
  const std::string label = node.label.substr(0, objectSuffix);
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
  llvm::cl::ParseCommandLineOptions(argc, argv, "Lotus object-expanded use-flow graph\n");
  cli::QueryOptions options;
  std::string optionError;
  if (!QueryOptionFlags.resolve(options, optionError)) {
    llvm::errs() << "lotus-ir-ufg: " << optionError << '\n';
    return 1;
  }
  SearchCompletion completion;
  bool query = Source != std::numeric_limits<unsigned>::max() ||
               Sink != std::numeric_limits<unsigned>::max();
  if ((Source == std::numeric_limits<unsigned>::max()) !=
      (Sink == std::numeric_limits<unsigned>::max())) {
    llvm::errs() << "lotus-ir-ufg: supply both --source-node and --sink-node\n";
    return 1;
  }
  if (PointerAnalysis != "andersen" && PointerAnalysis != "fspta") {
    llvm::errs() << "lotus-ir-ufg: --pta must be andersen or fspta\n";
    return 1;
  }
  if (SVFGMemorySSA != "auto" && SVFGMemorySSA != "on" && SVFGMemorySSA != "off") {
    llvm::errs() << "lotus-ir-ufg: --svfg-mssa must be auto, on, or off\n";
    return 1;
  }
  if (SVFGMemoryPartition != "distinct" &&
      SVFGMemoryPartition != "intra-disjoint" &&
      SVFGMemoryPartition != "inter-disjoint") {
    llvm::errs() << "lotus-ir-ufg: --svfg-memory-partition must be distinct, "
                    "intra-disjoint, or inter-disjoint\n";
    return 1;
  }
  if (ObjectGranularity != "site" && ObjectGranularity != "abstract") {
    llvm::errs() << "lotus-ir-ufg: --object-granularity must be site or abstract\n";
    return 1;
  }
  if (Format != "text" && Format != "json" && Format != "dot") {
    llvm::errs() << "lotus-ir-ufg: --format must be text, json, or dot\n";
    return 1;
  }
  if (query && Format != "text") {
    llvm::errs() << "lotus-ir-ufg: queries require --format=text\n";
    return 1;
  }
  if (!Check.empty() && query) {
    llvm::errs() << "lotus-ir-ufg: --check cannot be combined with a node query\n";
    return 1;
  }
  if (!Check.empty() && Format != "text") {
    llvm::errs() << "lotus-ir-ufg: --check requires --format=text\n";
    return 1;
  }
  if (!Check.empty() && Check != "double-free" && Check != "use-after-free" &&
      Check != "memory-leak" && Check != "file-leak") {
    llvm::errs() << "lotus-ir-ufg: unsupported --check rule\n";
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
    // Same labels as lotus-ir-usetracessa: --quiet checks skip printing the
    // module, and finding sites stay comparable across the two tools.
    historyOptions.instructionText = Check.empty() || !Quiet || FullLabels;
    historyOptions.allocationSiteObjects = ObjectGranularity == "site";
    historyOptions.heapObjectsOnly = HeapObjectsOnly;
    historyOptions.passCalls = PassCalls;
    if (flowSensitive) historyOptions.pointsTo = std::cref(*flowSensitive);
    auto result =
        buildUseTraceSSAFromLotusSVFG(*svfg, *module, mode, historyOptions);
    const auto builtShared = Clock::now();
    lotus::ufg::UFGGraph expanded(result.graph);
    const auto builtHistory = Clock::now();
    std::optional<DefectScan> checkReport;
    std::map<std::string, std::vector<std::size_t>> findingGroups;
    std::map<std::string, std::set<ObjectID>> findingObjects;
    std::optional<QueryResult> nodeQuery;
    std::optional<ObjectID> queryObject;
    if (!Check.empty()) {
      DefectKind kind = Check == "double-free" ? DefectKind::DoubleFree :
                        Check == "use-after-free" ? DefectKind::UseAfterFree :
                        Check == "memory-leak" ? DefectKind::MemoryLeak :
                                                 DefectKind::FileLeak;
      checkReport = lotus::ufg::DefectDetector(expanded, options.depth, options.limits, options.mode)
                        .scan(kind);
      completion.merge(checkReport->completion);
      for (std::size_t index = 0; index < checkReport->findings.size(); ++index) {
        const auto &witness = checkReport->findings[index].result.nodes;
        if (witness.empty()) continue;
        const auto &sink = expanded.graph().node(witness.back());
        std::string site = findingSite(sink);
        findingGroups[site].push_back(index);
        const auto &objects = checkReport->findings[index].objects;
        findingObjects[site].insert(objects.begin(), objects.end());
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
      if (Object.getNumOccurrences() &&
          !std::binary_search(expanded.objects().begin(), expanded.objects().end(), Object))
        throw std::invalid_argument("requested object is absent from the UFG universe");
      for (auto object : expanded.objects()) {
        if (Object.getNumOccurrences() && object != Object) continue;
        auto answer = expanded.runObject(request, object);
        completion.merge(answer.completion);
        if (!nodeQuery || answer.found() ||
            (answer.status == QueryStatus::Unknown &&
             nodeQuery->status == QueryStatus::NotFound)) {
          nodeQuery = std::move(answer);
          queryObject = object;
        }
        if (nodeQuery->found()) break;
      }
      if (!nodeQuery) {
        nodeQuery.emplace();
        nodeQuery->status = QueryStatus::Unknown;
        nodeQuery->completion.modelComplete = false;
        completion.modelComplete = false;
        nodeQuery->message = "no object lanes were constructed";
      }
    }
    const auto analyzed = Clock::now();
    if (Format == "json") expanded.printJSON(std::cout);
    else if (Format == "dot") expanded.printDOT(std::cout);
    else {
      const auto stats = result.graph.statistics();
      const auto ufgStats = expanded.statistics();
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
                << " ufg_objects=" << ufgStats.objects
                << " ufg_nodes=" << ufgStats.nodes
                << " ufg_edges=" << ufgStats.edges
                << " issues=" << result.graph.issues().size()
                << " excluded_nonheap_objects=" << result.excludedNonHeapObjects << '\n';
      if (!Quiet)
        for (const auto &issue : result.graph.issues())
          std::cout << "issue: " << issue << '\n';
      if (checkReport) {
        const auto &scan = *checkReport;
        std::cout << "check=" << Check << " result=" << statusName(scan.status)
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
                  << " candidate_objects=" << qs.candidateObjects
                  << " found_objects=" << qs.foundObjects
                  << " notfound_objects=" << qs.notFoundObjects
                  << " unknown_objects=" << qs.unknownObjects << '\n';
        if (!Quiet && !scan.message.empty())
          std::cout << "message: " << scan.message << '\n';
        if (!Quiet)
          for (const auto &group : findingGroups) {
            std::cout << "finding_site=" << group.first
                      << " candidates=" << group.second.size()
                      << " objects=" << findingObjects.at(group.first).size() << '\n';
            for (auto id : scan.findings[group.second.front()].result.nodes) {
              const auto &node = expanded.graph().node(id);
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
                  << " object=" << (queryObject ? std::to_string(*queryObject) : "none")
                  << " witness_edges=" << answer.edges.size()
                  << " product_states=" << answer.productStates
                  << " edges_examined=" << answer.edgesExamined
                  << " summary_pairs=" << answer.summaryPairs;
        cli::printCompletion(std::cout, completion);
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
          std::vector<cli::WitnessStep> steps;
          for (auto id : checkReport->findings[index].result.nodes) {
            const auto &node = expanded.graph().node(id);
            steps.emplace_back(node.label, node.sourceLocation);
          }
          return steps;
        };
      StableObjectKeys objectKeys(*svfg, *module);
      cli::writeResultsJSON(
          out, "ufg", Check,
          {{"object_granularity", ObjectGranularity},
           {"pta", PointerAnalysis},
           {"heap_objects_only", HeapObjectsOnly ? "true" : "false"}},
          *checkReport,
          [&](std::size_t index) {
            const auto &nodes = checkReport->findings[index].result.nodes;
            return nodes.empty() ? std::string("unknown")
                                 : findingSite(expanded.graph().node(nodes.back()));
          },
          witnessOf, objectKeys,
          [&](ObjectID object) { return objectKeys.location(object); });
    }
    if (Timing) {
      auto millis = [](Clock::time_point from, Clock::time_point to) {
        return std::chrono::duration<double, std::milli>(to - from).count();
      };
      std::cerr << std::fixed << std::setprecision(3)
                << "timing_ms parse=" << millis(start, parsed)
                << " svfg=" << millis(parsed, builtSVFG)
                << " pta=" << millis(builtSVFG, builtPTA)
                << " shared_temporal=" << millis(builtPTA, builtShared)
                << " ufg_expand=" << millis(builtShared, builtHistory)
                << " ufg=" << millis(builtPTA, builtHistory)
                << " build=" << millis(builtPTA, builtHistory)
                << " check=" << millis(builtHistory, analyzed)
                << " analysis=" << millis(builtPTA, analyzed)
                << " total=" << millis(start, analyzed) << '\n';
    }
    cli::explainIncomplete(std::cerr, completion);
  } catch (const std::exception &error) {
    llvm::errs() << "lotus-ir-ufg: " << error.what() << '\n';
    return 1;
  }
  return completion.searchComplete ? 0 : 2;
}
