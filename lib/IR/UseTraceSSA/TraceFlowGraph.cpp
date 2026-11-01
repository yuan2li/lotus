#include "IR/UseTraceSSA/TraceFlowGraph.h"

#include <algorithm>
#include <iomanip>
#include <ostream>
#include <sstream>
#include <stdexcept>
#include <unordered_set>

namespace lotus {
namespace usetracessa {
namespace {
FlowNodeID checkedID(std::size_t n) {
  if (n >= InvalidFlowID) throw std::length_error("UseTraceSSA: flow graph too large");
  return static_cast<FlowNodeID>(n);
}
std::string quoted(const std::string &s) {
  std::ostringstream out;
  out << '"';
  for (unsigned char c : s) {
    switch (c) {
    case '"': out << "\\\""; break;
    case '\\': out << "\\\\"; break;
    case '\n': out << "\\n"; break;
    case '\r': out << "\\r"; break;
    case '\t': out << "\\t"; break;
    default:
      if (c < 32) out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                      << static_cast<unsigned>(c) << std::dec;
      else out << c;
    }
  }
  out << '"';
  return out.str();
}
void printObjects(std::ostream &out, const ObjectSet &objects) {
  if (objects.isUnknown()) { out << "null"; return; }
  out << '[';
  bool first = true;
  for (auto object : objects.objects()) {
    if (!first) out << ',';
    first = false; out << quoted(std::to_string(object));
  }
  out << ']';
}
const char *kindName(FlowKind kind) {
  switch (kind) {
  case FlowKind::History: return "history";
  case FlowKind::Direct: return "direct";
  case FlowKind::Memory: return "memory";
  case FlowKind::Call: return "call";
  case FlowKind::Return: return "return";
  case FlowKind::Summary: return "summary";
  case FlowKind::Dependence: return "dependence";
  case FlowKind::Thread: return "thread";
  }
  return "invalid";
}
} // namespace
ObjectSet ObjectSet::unknown() { return {}; }
ObjectSet ObjectSet::known(std::vector<ObjectID> objects) {
  std::sort(objects.begin(), objects.end());
  objects.erase(std::unique(objects.begin(), objects.end()), objects.end());
  ObjectSet result;
  result.Unknown = false;
  if (!objects.empty())
    result.Objects =
        std::make_shared<const std::vector<ObjectID>>(std::move(objects));
  return result;
}
const std::vector<ObjectID> &ObjectSet::objects() const {
  static const std::vector<ObjectID> empty;
  return Objects ? *Objects : empty;
}
bool ObjectSet::contains(ObjectID object) const {
  return Unknown ||
         std::binary_search(objects().begin(), objects().end(), object);
}
bool ObjectSet::intersects(const ObjectSet &other) const {
  if (empty() || other.empty()) return false;
  if (Unknown || other.Unknown) return true;
  const auto &left = objects(), &right = other.objects();
  std::size_t i = 0, j = 0;
  while (i < left.size() && j < right.size()) {
    if (left[i] == right[j])
      return true;
    if (left[i] < right[j])
      ++i;
    else
      ++j;
  }
  return false;
}
FlowNodeID TraceFlowGraph::addNode(FlowNode node) {
  node.id = checkedID(Nodes.size());
  Nodes.push_back(std::move(node));
  ResourceUniverse.reset();
  Out.emplace_back(); In.emplace_back(); ++Revision;
  return Nodes.back().id;
}
FlowEdgeID TraceFlowGraph::addEdge(FlowEdge edge) {
  if (edge.from >= Nodes.size() || edge.to >= Nodes.size())
    throw std::out_of_range("UseTraceSSA: invalid flow edge endpoint");
  if ((edge.kind == FlowKind::Call || edge.kind == FlowKind::Return) &&
      edge.callSite == NoNativeID)
    throw std::invalid_argument("UseTraceSSA: call/return edge needs a call-site ID");
  edge.id = checkedID(Edges.size());
  Edges.push_back(std::move(edge));
  ResourceUniverse.reset();
  Out[Edges.back().from].push_back(Edges.back().id);
  In[Edges.back().to].push_back(Edges.back().id);
  ++Revision;
  return Edges.back().id;
}
void TraceFlowGraph::addHistory(FunctionID function, std::string name, Graph history) {
  if (function == InvalidID || Layers.count(function))
    throw std::invalid_argument("UseTraceSSA: duplicate/invalid layer ID");
  // Graph::build verifies before returning. Graph's representation is private
  // and immutable afterwards, so importing it must not repeat that full pass.
  HistoryLayer entry;
  entry.function = function; entry.name = std::move(name);
  entry.history = std::move(history);
  for (const Node &n : entry.history.nodes()) {
    FlowNode node;
    node.function = function; node.version = n.id;
    node.label = entry.name + ":" + entry.history.program().values().at(n.value) +
                 "." + std::to_string(n.id);
    if (n.site != InvalidID)
      node.label += " " + entry.history.program().operations().at(n.site).label;
    else if (n.kind == NodeKind::Phi) node.label += " history-phi";
    entry.versions.push_back(addNode(std::move(node)));
  }
  for (const Node &n : entry.history.nodes())
    for (const auto &pred : n.incoming) {
      FlowEdge edge;
      edge.from = entry.versions.at(pred.version); edge.to = entry.versions.at(n.id);
      edge.kind = FlowKind::History; edge.predecessor = pred.predecessor;
      addEdge(std::move(edge));
    }
  Layers.emplace(function, std::move(entry));
  ++Revision;
}
const HistoryLayer &TraceFlowGraph::layer(FunctionID function) const {
  return Layers.at(function);
}
FlowNodeID TraceFlowGraph::version(FunctionID f, VersionID v) const {
  return layer(f).versions.at(v);
}
FlowNodeID TraceFlowGraph::definition(FunctionID f, ValueID v) const {
  auto d = layer(f).history.definition(v);
  if (d == InvalidID) throw std::invalid_argument("UseTraceSSA: unreachable definition port");
  return version(f, d);
}
FlowNodeID TraceFlowGraph::before(FunctionID f, SiteID s, ValueID v) const {
  auto *u = layer(f).history.use(s, v);
  if (!u) throw std::invalid_argument("UseTraceSSA: missing before-use port");
  return version(f, u->before);
}
FlowNodeID TraceFlowGraph::after(FunctionID f, SiteID s, ValueID v) const {
  auto *u = layer(f).history.use(s, v);
  if (!u) throw std::invalid_argument("UseTraceSSA: missing after-use port");
  return version(f, u->after);
}
void TraceFlowGraph::annotate(FlowNodeID id, Event e, Certainty c) {
  auto &n = Nodes.at(id);
  if (n.events != Event::None && n.certainty != c)
    throw std::invalid_argument("UseTraceSSA: split events with different certainties into nodes");
  n.events = n.events | e; n.certainty = c; ++Revision;
  ResourceUniverse.reset();
}
void TraceFlowGraph::annotate(FlowNodeID id, Event events, ObjectSet objects, Certainty c) {
  if (events == Event::None) throw std::invalid_argument("UseTraceSSA: empty guarded effect");
  if (c != Certainty::Must && c != Certainty::May)
    throw std::invalid_argument("UseTraceSSA: invalid effect certainty");
  Nodes.at(id).effects.push_back({events, std::move(objects), c});
  ResourceUniverse.reset();
  ++Revision;
}
EffectiveEvent TraceFlowGraph::effectiveEvent(FlowNodeID id, ObjectID object) const {
  const auto &n = node(id);
  EffectiveEvent result{n.events, n.certainty};
  for (const auto &effect : n.effects) if (effect.objects.contains(object)) {
    result.events = result.events | effect.events;
    if (effect.certainty == Certainty::May || effect.objects.isUnknown())
      result.certainty = Certainty::May;
  }
  return result;
}
std::vector<ObjectID> TraceFlowGraph::resourceCandidates() const {
  if (ResourceUniverse) return *ResourceUniverse;
  std::vector<ObjectID> result;
  std::unordered_set<const std::vector<ObjectID> *> guards;
  bool unknown = false;
  for (const auto &n : Nodes) for (const auto &effect : n.effects) {
      if (effect.objects.isUnknown())
        unknown = true;
      else if (guards.insert(&effect.objects.objects()).second)
        result.insert(result.end(), effect.objects.objects().begin(),
                      effect.objects.objects().end());
  }
  for (const auto &edge : Edges)
    if (!edge.objects.isUnknown() &&
        guards.insert(&edge.objects.objects()).second)
      result.insert(result.end(), edge.objects.objects().begin(), edge.objects.objects().end());
  if (unknown)
    result.push_back(UnknownResource);
  std::sort(result.begin(), result.end());
  result.erase(std::unique(result.begin(), result.end()), result.end());
  return result;
}
void TraceFlowGraph::setResourceUniverse(std::vector<ObjectID> objects) {
  std::sort(objects.begin(), objects.end());
  objects.erase(std::unique(objects.begin(), objects.end()), objects.end());
  ResourceUniverse = std::move(objects);
  ++Revision;
}
FlowStatistics TraceFlowGraph::statistics() const {
  FlowStatistics s;
  s.flowEdges = Edges.size();
  for (const auto &layer : Layers) for (const auto &n : layer.second.history.nodes()) {
    ++s.historyNodes;
    s.historyPsiNodes += n.kind == NodeKind::Psi;
    s.historyPhiNodes += n.kind == NodeKind::Phi;
  }
  auto count = [&](const ObjectSet &objects) {
    if (objects.isUnknown()) ++s.unknownObjectSets;
    else s.knownObjectCardinality += objects.objects().size();
  };
  for (const auto &n : Nodes) for (const auto &effect : n.effects) {
    ++s.guardedEffects;
    count(effect.objects);
    // Acquisitions name their own object; k measures aliasing at uses.
    if (!effect.objects.isUnknown() &&
        !hasEvent(effect.events, Event::Allocate | Event::Open))
      s.effectGuardSizes.push_back(effect.objects.objects().size());
  }
  for (const auto &e : Edges) count(e.objects);
  return s;
}
void TraceFlowGraph::setNative(FlowNodeID id, NativeID native) {
  auto &n = Nodes.at(id);
  if (n.native != NoNativeID && n.native != native)
    throw std::invalid_argument("UseTraceSSA: conflicting native node provenance");
  n.native = native; ++Revision;
}
void TraceFlowGraph::setSourceLocation(FlowNodeID id, std::string location) {
  Nodes.at(id).sourceLocation = std::move(location);
  ++Revision;
}
void TraceFlowGraph::disableEdge(FlowEdgeID id) {
  Edges.at(id).enabled = false; ResourceUniverse.reset(); ++Revision;
}
void TraceFlowGraph::addIssue(std::string issue) { Issues.push_back(std::move(issue)); ++Revision; }
std::vector<FlowNodeID> TraceFlowGraph::select(Event events) const {
  std::vector<FlowNodeID> result;
  for (const auto &n : Nodes) {
    bool selected = hasEvent(n.events, events);
    for (const auto &effect : n.effects)
      selected |= !effect.objects.empty() && hasEvent(effect.events, events);
    if (selected) result.push_back(n.id);
  }
  return result;
}
bool TraceFlowGraph::verify(std::string *error) const {
  auto fail = [&](const char *s) { if (error) *error = s; return false; };
  if (Nodes.size() != Out.size() || Nodes.size() != In.size()) return fail("adjacency size");
  std::vector<unsigned> seenOut(Edges.size()), seenIn(Edges.size());
  for (std::size_t i = 0; i < Nodes.size(); ++i) {
    if (Nodes[i].id != i) return fail("node ID mismatch");
    if (Nodes[i].certainty != Certainty::Must && Nodes[i].certainty != Certainty::May)
      return fail("invalid certainty");
    for (const auto &effect : Nodes[i].effects) {
      if (effect.events == Event::None) return fail("empty guarded effect");
      if (effect.certainty != Certainty::Must && effect.certainty != Certainty::May)
        return fail("invalid guarded certainty");
      const auto &objects = effect.objects.objects();
      if ((effect.objects.isUnknown() && !objects.empty()) ||
          !std::is_sorted(objects.begin(), objects.end()) ||
          std::adjacent_find(objects.begin(), objects.end()) != objects.end())
        return fail("malformed guarded object set");
    }
    for (auto e : Out[i]) {
      if (e >= Edges.size() || Edges[e].from != i) return fail("outgoing edge mismatch");
      ++seenOut[e];
    }
    for (auto e : In[i]) {
      if (e >= Edges.size() || Edges[e].to != i) return fail("incoming edge mismatch");
      ++seenIn[e];
    }
  }
  for (std::size_t i = 0; i < Edges.size(); ++i) {
    const auto &e = Edges[i];
    if (e.id != i || seenOut[i] != 1 || seenIn[i] != 1) return fail("edge ID/adjacency mismatch");
    if ((e.kind == FlowKind::Call || e.kind == FlowKind::Return) && e.callSite == NoNativeID)
      return fail("missing call-site ID");
  }
  if (error) error->clear();
  return true;
}
void TraceFlowGraph::printDOT(std::ostream &out) const {
  out << "digraph UseTraceSSA {\n";
  for (const auto &n : Nodes) {
    std::ostringstream label;
    label << n.label << "\nevents=" << static_cast<std::uint32_t>(n.events)
          << " " << (n.certainty == Certainty::Must ? "must" : "may");
    for (const auto &effect : n.effects) {
      label << "\neffect=" << static_cast<std::uint32_t>(effect.events)
            << " " << (effect.certainty == Certainty::Must ? "must" : "may") << " @";
      printObjects(label, effect.objects);
    }
    out << "  n" << n.id << " [label=" << quoted(label.str()) << "];\n";
  }
  for (const auto &e : Edges) if (e.enabled) {
    std::ostringstream label;
    label << kindName(e.kind);
    if (!e.guard.empty()) label << ':' << e.guard;
    label << " @";
    printObjects(label, e.objects);
    out << "  n" << e.from << " -> n" << e.to << " [label="
        << quoted(label.str())
        << "];\n";
  }
  out << "}\n";
}
void TraceFlowGraph::printJSON(std::ostream &out) const {
  out << "{\"schema\":\"lotus-usetracessa-2\",\"complete\":" << (complete() ? "true" : "false")
      << ",\"issues\":[";
  for (std::size_t i = 0; i < Issues.size(); ++i) out << (i ? "," : "") << quoted(Issues[i]);
  out << "],\"resource_universe\":";
  if (!ResourceUniverse) out << "null";
  else {
    out << '[';
    for (std::size_t i = 0; i < ResourceUniverse->size(); ++i)
      out << (i ? "," : "") << quoted(std::to_string((*ResourceUniverse)[i]));
    out << ']';
  }
  out << ",\"nodes\":[";
  for (const auto &n : Nodes) {
    out << (n.id ? "," : "") << "{\"id\":" << n.id
        << ",\"label\":" << quoted(n.label)
        << ",\"source_location\":" << quoted(n.sourceLocation)
        << ",\"function\":" << n.function << ",\"version\":" << n.version
        << ",\"native\":" << quoted(std::to_string(n.native))
        << ",\"events\":" << static_cast<std::uint32_t>(n.events)
        << ",\"certainty\":"
        << quoted(n.certainty == Certainty::Must ? "must" : "may")
        << ",\"effects\":[";
    bool first = true;
    for (const auto &effect : n.effects) {
      if (!first) out << ',';
      first = false;
      out << "{\"events\":" << static_cast<std::uint32_t>(effect.events)
          << ",\"certainty\":" << quoted(effect.certainty == Certainty::Must ? "must" : "may")
          << ",\"objects\":";
      printObjects(out, effect.objects);
      out << '}';
    }
    out << "]}";
  }
  out << "],\"edges\":[";
  for (const auto &e : Edges) {
    out << (e.id ? "," : "") << "{\"id\":" << e.id << ",\"from\":" << e.from
        << ",\"to\":" << e.to << ",\"kind\":" << quoted(kindName(e.kind))
        << ",\"callsite\":" << quoted(std::to_string(e.callSite))
        << ",\"native\":" << quoted(std::to_string(e.native))
        << ",\"native_kind\":" << e.nativeKind << ",\"predecessor\":" << e.predecessor
        << ",\"enabled\":" << (e.enabled ? "true" : "false")
        << ",\"guard\":" << quoted(e.guard) << ",\"objects\":";
    printObjects(out, e.objects);
    out << '}';
  }
  out << "]}\n";
}
} // namespace usetracessa
} // namespace lotus
