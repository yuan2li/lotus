#include "IR/UFG/DefectDetector.h"
#include "IR/UFG/Search.h"

#include <algorithm>
#include <stdexcept>

namespace lotus {
namespace ufg {
using namespace usetracessa;
namespace {
DefectReport makeReport(const UFGGraph &graph, DefectKind kind, QueryResult result,
                        ObjectID object) {
  DefectReport report;
  report.kind = kind;
  report.result = std::move(result);
  report.witnessObject = object;
  report.objects = {object};
  for (auto id : report.result.nodes) {
    NativeID native = graph.graph().node(id).native;
    if (native != NoNativeID) report.nativeWitness.push_back(native);
  }
  return report;
}
DefectReport makeGenericReport(const TraceFlowGraph &graph, DefectKind kind,
                               QueryResult result) {
  DefectReport report;
  report.kind = kind;
  report.result = std::move(result);
  for (auto id : report.result.nodes) {
    NativeID native = graph.node(id).native;
    if (native != NoNativeID) report.nativeWitness.push_back(native);
  }
  return report;
}
} // namespace

DefectScan DefectDetector::scan(DefectKind kind) const {
  if (kind != DefectKind::DoubleFree && kind != DefectKind::UseAfterFree &&
      kind != DefectKind::MemoryLeak && kind != DefectKind::FileLeak)
    throw std::invalid_argument("UFG: scan supports resource rules only");
  DefectScan scan;
  const auto &source = Graph.source();
  Query query = kind == DefectKind::DoubleFree ? queries::doubleFree(source) :
                kind == DefectKind::UseAfterFree ? queries::useAfterFree(source) :
                kind == DefectKind::MemoryLeak ? queries::memoryLeak(source) :
                                                 queries::fileLeak(source);
  query.contextLimit = ContextLimit;
  query.context = Context;
  Limits.apply(query);
  const Event required = kind == DefectKind::FileLeak ? Event::Open :
      kind == DefectKind::MemoryLeak ? Event::Allocate : Event::Release;
  if (source.select(required).empty() || query.sinks.empty()) {
    scan.status = QueryStatus::Unknown;
    scan.exhaustive = false;
    scan.completion.modelComplete = false;
    scan.message = "required defect facts were not supplied";
    return scan;
  }
  scan.statistics.candidateObjects = Graph.objects().size();
  scan.exhaustive = source.complete();
  scan.completion.modelComplete = source.complete();
  std::map<FlowNodeID, std::size_t> findings;
  for (ObjectID object : Graph.objects()) {
    Query laneQuery = query;
    const Event target = kind == DefectKind::DoubleFree ? Event::Release :
                         kind == DefectKind::UseAfterFree ? Event::Dereference :
                                                           Event::Exit;
    laneQuery.sinks.erase(std::remove_if(laneQuery.sinks.begin(), laneQuery.sinks.end(),
        [&](FlowNodeID sink) {
          return !hasEvent(Graph.graph().node(Graph.node(object, sink)).events, target);
        }), laneQuery.sinks.end());
    auto lane = SearchEngine(Graph).scan(std::move(laneQuery), object);
    scan.completion.merge(lane.completion);
    scan.statistics.productStates += lane.productStates;
    scan.statistics.productEdges += lane.productEdges;
    scan.statistics.edgesExamined += lane.edgesExamined;
    scan.statistics.summaryPairs += lane.summaryPairs;
    scan.exhaustive &= lane.exhaustive;
    for (auto &hit : lane.foundAt) {
      auto it = findings.find(hit.first);
      if (it == findings.end()) {
        findings.emplace(hit.first, scan.findings.size());
        scan.findings.push_back(makeReport(Graph, kind, std::move(hit.second), object));
      } else {
        scan.findings[it->second].objects.push_back(object);
      }
    }
    auto status = !lane.foundAt.empty() ? QueryStatus::Found
                  : lane.status == QueryStatus::Unknown ? QueryStatus::Unknown
                                                        : QueryStatus::NotFound;
    scan.objectStatus.emplace(object, status);
    if (status == QueryStatus::Found) ++scan.statistics.foundObjects;
    else if (status == QueryStatus::Unknown) ++scan.statistics.unknownObjects;
    else ++scan.statistics.notFoundObjects;
  }
  std::sort(scan.findings.begin(), scan.findings.end(), [&](const DefectReport &a,
                                                            const DefectReport &b) {
    return Graph.originalNode(a.result.nodes.back()) <
           Graph.originalNode(b.result.nodes.back());
  });
  scan.status = !scan.findings.empty() ? QueryStatus::Found :
                scan.exhaustive ? QueryStatus::NotFound : QueryStatus::Unknown;
  scan.message = scan.exhaustive ? "object-expanded reachability complete" :
                                   "incomplete model or query budget exhausted";
  return scan;
}

DefectReport DefectDetector::run(DefectKind kind, std::vector<FlowNodeID> roots,
                                 std::vector<FlowNodeID> uses) const {
  if (kind == DefectKind::Taint || kind == DefectKind::UncheckedUse) {
    Query query = kind == DefectKind::Taint ? queries::taint(Graph.source()) :
        queries::uncheckedUse(std::move(roots), std::move(uses));
    query.contextLimit = ContextLimit;
    query.context = Context;
    Limits.apply(query);
    QueryResult result;
    if (query.sources.empty() || query.sinks.empty()) {
      result.status = QueryStatus::Unknown;
      result.completion.modelComplete = false;
      result.message = "required defect facts were not supplied";
    } else {
      result = SearchEngine(Graph).runGeneric(std::move(query));
    }
    return makeGenericReport(Graph.source(), kind, std::move(result));
  }
  auto result = scan(kind);
  if (!result.findings.empty()) {
    result.findings.front().result.completion = result.completion;
    return std::move(result.findings.front());
  }
  DefectReport report;
  report.kind = kind;
  report.result.status = result.status;
  report.result.completion = result.completion;
  report.result.message = std::move(result.message);
  return report;
}

} // namespace ufg
} // namespace lotus
