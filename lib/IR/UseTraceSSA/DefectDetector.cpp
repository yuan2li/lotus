#include "IR/UseTraceSSA/DefectDetector.h"

#include <chrono>
#include <stdexcept>

namespace lotus {
namespace usetracessa {
namespace {
DefectReport makeReport(const TraceFlowGraph &graph, DefectKind kind, QueryResult result) {
  DefectReport report;
  report.kind = kind; report.result = std::move(result);
  if (!report.result.nodes.empty())
    report.sink = report.result.nodes.back();
  for (auto id : report.result.nodes) {
    NativeID native = graph.node(id).native;
    if (native != NoNativeID) report.nativeWitness.push_back(native);
  }
  return report;
}
ObjectUniverse candidates(const TraceFlowGraph &graph) {
  auto objects = graph.resourceCandidates();
  // Generic resource annotations have no finite guard. Keep an anonymous
  // candidate for these clients as well as for unknown resource effects.
  for (const auto &n : graph.nodes())
    if (hasEvent(n.events, Event::Allocate | Event::Release |
                               Event::Dereference | Event::Open | Event::Close)) {
      objects.push_back(UnknownResource); break;
    }
  return ObjectUniverse(std::move(objects));
}
} // namespace

DefectReport DefectScan::materialize(std::size_t finding,
                                     const TraceFlowGraph &graph,
                                     std::optional<ObjectID> object) const {
  const auto &report = findings.at(finding);
  if (!object || object == report.witnessObject)
    object = report.witnessObject;
  if (!report.result.nodes.empty() && object == report.witnessObject)
    return report;
  if (SourceGraph != &graph || graph.revision() != GraphRevision)
    throw std::invalid_argument("UseTraceSSA: deferred witness graph changed");
  QueryResult witness;
  if (Witnesses && Witnesses->hasWitnesses())
    witness = Witnesses->witness(report.sink, *object);
  else if (WitnessQuery) {
    Query q = *WitnessQuery;
    q.sinks = {report.sink};
    q.memoryObject = object;
    witness = QueryEngine(graph).run(q);
  } else {
    witness.status = QueryStatus::Unknown;
    witness.completion = completion;
    witness.message = "defect scan did not retain witness provenance";
  }
  auto result = makeReport(graph, report.kind, std::move(witness));
  result.sink = report.sink;
  result.witnessObject = object;
  result.objects = report.objects;
  result.objectCount = report.objectCount;
  return result;
}

DefectReport DefectDetector::run(DefectKind kind, std::vector<FlowNodeID> roots,
                                 std::vector<FlowNodeID> uses) const {
  if (kind == DefectKind::DoubleFree || kind == DefectKind::UseAfterFree ||
      kind == DefectKind::MemoryLeak || kind == DefectKind::FileLeak) {
    auto scanResult = scanImpl(kind, true);
    if (!scanResult.findings.empty()) {
      scanResult.findings.front().result.completion = scanResult.completion;
      return std::move(scanResult.findings.front());
    }
    QueryResult result; result.status = scanResult.status; result.message = scanResult.message;
    result.completion = scanResult.completion;
    return makeReport(Graph, kind, std::move(result));
  }
  Query q;
  if (kind == DefectKind::Taint) q = queries::taint(Graph);
  else q = queries::uncheckedUse(std::move(roots), std::move(uses));
  q.contextLimit = ContextLimit;
  q.context = Context;
  Limits.apply(q);
  QueryResult result;
  if (q.sources.empty() || q.sinks.empty()) {
    result.status = QueryStatus::Unknown;
    result.completion.modelComplete = false;
    result.message = "required defect facts were not supplied";
  } else result = QueryEngine(Graph).run(q);
  return makeReport(Graph, kind, std::move(result));
}

DefectScan DefectDetector::scan(DefectKind kind,
                                DefectScanOptions options) const {
  return scanImpl(kind, false, options);
}

DefectReport DefectDetector::findAny(DefectKind kind) const {
  auto scan = scanImpl(kind, true, {true, true, true});
  if (!scan.findings.empty())
    return std::move(scan.findings.front());
  QueryResult result;
  result.status = scan.status;
  result.completion = scan.completion;
  result.message = scan.message;
  return makeReport(Graph, kind, std::move(result));
}

DefectScan DefectDetector::scanImpl(DefectKind kind, bool firstOnly,
                                    DefectScanOptions options) const {
  if (kind != DefectKind::DoubleFree && kind != DefectKind::UseAfterFree &&
      kind != DefectKind::MemoryLeak && kind != DefectKind::FileLeak)
    throw std::invalid_argument("UseTraceSSA: scan supports resource rules only");
  DefectScan scan;
  scan.SourceGraph = &Graph;
  scan.GraphRevision = Graph.revision();
  Query q = kind == DefectKind::DoubleFree ? queries::doubleFree(Graph) :
            kind == DefectKind::UseAfterFree ? queries::useAfterFree(Graph) :
            kind == DefectKind::MemoryLeak ? queries::memoryLeak(Graph) :
                                             queries::fileLeak(Graph);
  q.contextLimit = ContextLimit;
  q.context = Context;
  Limits.apply(q);
  const Event required = kind == DefectKind::FileLeak ? Event::Open :
      kind == DefectKind::MemoryLeak ? Event::Allocate : Event::Release;
  if (Graph.select(required).empty() || q.sinks.empty()) {
    scan.status = QueryStatus::Unknown; scan.exhaustive = false;
    scan.completion.modelComplete = false;
    scan.message = "required defect facts were not supplied";
    return scan;
  }
  // Reuse symbolic path provenance when available. Unbounded Dyck queries
  // share witness tabulation across sinks choosing the same object.
  const Event target = kind == DefectKind::DoubleFree     ? Event::Release
                       : kind == DefectKind::UseAfterFree ? Event::Dereference
                                                          : Event::Exit;
  q.sinkEvents = target;
  using Clock = std::chrono::steady_clock;
  const auto searchStarted = Clock::now();
  if (options.fixedObjects) {
    scanFixedObjects(kind, q, firstOnly, options, scan);
    scan.searchMilliseconds =
        std::chrono::duration<double, std::milli>(Clock::now() - searchStarted)
            .count();
    return scan;
  }
  auto batch = QueryEngine(Graph).runObjects(
      {q, candidates(Graph),
       options.materializeWitnesses || options.retainWitnesses,
       options.firstFinding,
       options.retainObjects || options.materializeWitnesses ||
           options.retainWitnesses});
  const auto reportStarted = Clock::now();
  scan.searchMilliseconds =
      std::chrono::duration<double, std::milli>(reportStarted - searchStarted)
          .count();
  scan.statistics = batch.statistics;
  for (auto object : batch.universe.objects())
    scan.objectStatus.emplace(object, batch.status(object));
  scan.completion = batch.completion;
  scan.exhaustive = batch.complete;
  if (!options.retainObjects && !options.materializeWitnesses &&
      !options.retainWitnesses) {
    for (const auto &sink : batch.sinks) {
      DefectReport report;
      report.kind = kind;
      report.sink = sink.sink;
      report.witnessObject = sink.representative;
      report.objectCount = sink.objects;
      report.result.status = QueryStatus::Found;
      report.result.completion = batch.completion;
      report.result.witnessComplete = false;
      scan.findings.push_back(std::move(report));
    }
    scan.status = !scan.findings.empty() ? QueryStatus::Found
                  : scan.exhaustive      ? QueryStatus::NotFound
                                         : QueryStatus::Unknown;
    scan.message = batch.message;
    scan.reportingMilliseconds =
        std::chrono::duration<double, std::milli>(Clock::now() - reportStarted)
            .count();
    return scan;
  }
  // Retained provenance also serves other objects at a materialized sink.
  if (options.retainWitnesses && !batch.hasWitnesses())
    scan.WitnessQuery = q;
  std::map<ObjectID, std::vector<FlowNodeID>> witnessSinks;
  std::map<FlowNodeID, std::vector<ObjectID>> objectsAtSink;
  for (const auto &sink : batch.foundAt) {
    // Query::sinkEvents already restricted acceptance to this object's target
    // event. Do not repeat effect/guard evaluation for every accepted bit.
    std::vector<ObjectID> atSink;
    for (auto bit : sink.second.set_bits()) {
      ObjectID object = batch.universe.objects()[bit];
      atSink.push_back(object);
    }
    if (atSink.empty()) continue;
    ObjectID object = atSink.front();
    if (!options.materializeWitnesses) {
      DefectReport report;
      report.kind = kind;
      report.sink = sink.first;
      report.witnessObject = object;
      report.objects = std::move(atSink);
      report.objectCount = report.objects.size();
      report.result.status = QueryStatus::Found;
      report.result.completion = batch.completion;
      report.result.witnessComplete = false;
      scan.findings.push_back(std::move(report));
      if (firstOnly)
        break;
      continue;
    }
    if (firstOnly || batch.hasWitnesses()) {
      QueryResult witness;
      if (batch.hasWitnesses()) witness = batch.witness(sink.first, object);
      else {
        q.sinks = {sink.first}; q.memoryObject = object;
        witness = QueryEngine(Graph).run(q);
      }
      scan.completion.merge(witness.completion);
      if (!witness.found()) {
        scan.exhaustive = false;
        scan.completion.stop(SearchStopReason::WitnessUnavailable);
        continue;
      }
      auto report = makeReport(Graph, kind, std::move(witness));
      report.sink = sink.first;
      report.witnessObject = object;
      report.objects = std::move(atSink);
      report.objectCount = report.objects.size();
      scan.findings.push_back(std::move(report));
      if (firstOnly) break;
      continue;
    }
    witnessSinks[object].push_back(sink.first);
    objectsAtSink.emplace(sink.first, std::move(atSink));
  }
  std::map<FlowNodeID, DefectReport> reports;
  for (const auto &group : witnessSinks) {
    q.sinks = group.second; q.memoryObject = group.first;
    auto witnesses = QueryEngine(Graph).runToSinks(q);
    scan.completion.merge(witnesses.completion);
    for (auto sink : group.second) {
      auto found = witnesses.foundAt.find(sink);
      if (found == witnesses.foundAt.end()) {
        scan.exhaustive = false;
        scan.completion.stop(SearchStopReason::WitnessUnavailable);
        continue;
      }
      auto report = makeReport(Graph, kind, std::move(found->second));
      report.sink = sink;
      report.witnessObject = group.first;
      report.objects = std::move(objectsAtSink.at(sink));
      report.objectCount = report.objects.size();
      reports.emplace(sink, std::move(report));
    }
  }
  for (auto &report : reports) scan.findings.push_back(std::move(report.second));
  scan.exhaustive = scan.completion.complete();
  scan.status = !scan.findings.empty() ? QueryStatus::Found :
                scan.exhaustive ? QueryStatus::NotFound : QueryStatus::Unknown;
  scan.message = batch.message;
  if (options.retainWitnesses)
    scan.Witnesses = std::make_shared<ObjectBatchResult>(std::move(batch));
  scan.reportingMilliseconds =
      std::chrono::duration<double, std::milli>(Clock::now() - reportStarted)
          .count();
  return scan;
}
void DefectDetector::scanFixedObjects(DefectKind kind, Query q, bool firstOnly,
                                      const DefectScanOptions &options,
                                      DefectScan &scan) const {
  // One independent query per object: each object pays its own traversal,
  // which is the P_fixed = R baseline for the batch scan.
  const auto universe = candidates(Graph);
  auto &stats = scan.statistics;
  stats.candidateObjects = universe.objects().size();
  std::map<FlowNodeID, std::vector<ObjectID>> objectsAtSink;
  std::map<FlowNodeID, QueryResult> witnessAtSink;
  bool stopped = false;
  q.scanWitnesses = options.materializeWitnesses;
  for (ObjectID object : universe.objects()) {
    if (stopped) {
      ++stats.unknownObjects;
      scan.objectStatus.emplace(object, QueryStatus::Unknown);
      continue;
    }
    q.memoryObject = object;
    auto answer = QueryEngine(Graph).runToSinks(q);
    scan.completion.merge(answer.completion);
    stats.productStates += answer.productStates;
    stats.edgesExamined += answer.edgesExamined;
    stats.summaryPairs += answer.summaryPairs;
    stats.recordPushes(answer.pushes, 1);
    auto status = !answer.foundAt.empty() ? QueryStatus::Found
                  : answer.complete       ? QueryStatus::NotFound
                                          : QueryStatus::Unknown;
    scan.objectStatus.emplace(object, status);
    if (status == QueryStatus::Found) ++stats.foundObjects;
    else if (status == QueryStatus::NotFound) ++stats.notFoundObjects;
    else ++stats.unknownObjects;
    if (!answer.complete) scan.exhaustive = false;
    for (auto &found : answer.foundAt) {
      objectsAtSink[found.first].push_back(object);
      witnessAtSink.emplace(found.first, std::move(found.second));
    }
    if (firstOnly && !answer.foundAt.empty()) {
      stopped = true;
      scan.exhaustive = false;
    }
  }
  for (auto &sink : objectsAtSink) {
    DefectReport report;
    if (options.materializeWitnesses)
      report = makeReport(Graph, kind, std::move(witnessAtSink.at(sink.first)));
    else {
      report.kind = kind;
      report.result.status = QueryStatus::Found;
      report.result.completion = scan.completion;
      report.result.witnessComplete = false;
    }
    report.sink = sink.first;
    report.witnessObject = sink.second.front();
    report.objectCount = sink.second.size();
    if (options.retainObjects || options.materializeWitnesses)
      report.objects = std::move(sink.second);
    scan.findings.push_back(std::move(report));
    if (firstOnly) break;
  }
  scan.exhaustive = scan.exhaustive && scan.completion.complete();
  scan.status = !scan.findings.empty() ? QueryStatus::Found
                : scan.exhaustive      ? QueryStatus::NotFound
                                       : QueryStatus::Unknown;
  if (options.retainWitnesses) {
    q.scanWitnesses = true;
    scan.WitnessQuery = q;
  }
}
} // namespace usetracessa
} // namespace lotus
