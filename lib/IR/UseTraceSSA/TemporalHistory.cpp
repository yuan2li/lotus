#include "IR/UseTraceSSA/TemporalHistory.h"

#include <stdexcept>

#include <llvm/ADT/ArrayRef.h>
#include <llvm/ADT/SmallVector.h>

namespace lotus {
namespace usetracessa {
FlowNodeID TemporalHistory::before(const TraceFlowGraph &g, SiteID site) const {
  return g.before(id, sites.at(site), execution);
}
FlowNodeID TemporalHistory::after(const TraceFlowGraph &g, SiteID site) const {
  return g.after(id, sites.at(site), execution);
}
FlowNodeID TemporalHistory::entry(const TraceFlowGraph &g) const {
  return g.definition(id, execution);
}
TemporalHistory TemporalHistory::append(TraceFlowGraph &g, FunctionID id,
                                        std::string name, const Program &cfg,
                                        const std::vector<TemporalEffect> &effects,
                                        bool annotateExits) {
  TemporalHistory result;
  result.id = id;
  for (const auto &effect : effects)
    if (effect.site >= cfg.operations().size())
      throw std::invalid_argument("UseTraceSSA: temporal effect outside CFG");
  Program p;
  for (const auto &b : cfg.blocks()) p.addBlock(b.name);
  if (cfg.blocks().empty()) {
    g.addHistory(id, std::move(name), Graph::build(std::move(p)));
    return result;
  }
  p.setEntry(cfg.entry());
  for (const auto &e : cfg.edges()) p.addEdge(e.from, e.to, e.label);
  result.execution = p.addValue("execution");
  p.addOperation(p.entry(), "temporal entry", {}, {result.execution});
  auto add = [&](SiteID old, BlockID block, EdgeID edge) {
    const auto &label = cfg.operations().at(old).label;
    result.sites[old] = edge == InvalidID ?
        p.addOperation(block, label, {result.execution}) :
        p.addEdgeOperation(edge, label, {result.execution});
  };
  std::vector<bool> successors(cfg.blocks().size());
  for (const auto &e : cfg.edges()) successors[e.from] = true;
  for (BlockID b = 0; b < cfg.blocks().size(); ++b) {
    for (auto site : cfg.blocks()[b].operations) add(site, b, InvalidID);
    if (!successors[b])
      result.exits[b] = p.addOperation(b, "temporal exit", {result.execution});
  }
  for (EdgeID e = 0; e < cfg.edges().size(); ++e)
    for (auto site : cfg.edges()[e].operations) add(site, InvalidID, e);
  g.addHistory(id, std::move(name), Graph::build(std::move(p)));
  for (const auto &effect : effects) {
    if (!g.layer(id).history.use(result.sites.at(effect.site), result.execution)) continue;
    auto n = result.after(g, effect.site);
    if (effect.events != Event::None)
      g.annotate(n, effect.events, effect.objects, effect.certainty);
    if (effect.native != NoNativeID) g.setNative(n, effect.native);
  }
  for (const auto &exit : result.exits)
    if (annotateExits && g.layer(id).history.use(exit.second, result.execution))
      g.annotate(g.after(id, exit.second, result.execution), Event::Exit);
  return result;
}
namespace {
void connectTargets(TraceFlowGraph &g, const TemporalHistory &caller,
                    SiteID site, CallSiteID callSite,
                    llvm::ArrayRef<const TemporalHistory *> targets,
                    bool completeTargets, ObjectSet objects) {
  if (callSite == NoNativeID || (targets.empty() && completeTargets))
    throw std::invalid_argument("UseTraceSSA: invalid temporal call");
  auto before = caller.before(g, site), after = caller.after(g, site);
  if (g.node(after).events != Event::None || !g.node(after).effects.empty())
    throw std::invalid_argument("UseTraceSSA: call splice requires a separate effect site");
  std::vector<FlowEdge> pending;
  for (const auto *targetPtr : targets) {
    const auto &target = *targetPtr;
    FlowEdge call;
    call.from = before; call.to = target.entry(g); call.kind = FlowKind::Call;
    call.callSite = callSite; call.objects = objects; pending.push_back(call);
    for (const auto &exit : target.exits) {
      if (!g.layer(target.id).history.use(exit.second, target.execution)) continue;
      FlowEdge ret;
      ret.from = g.after(target.id, exit.second, target.execution); ret.to = after;
      ret.kind = FlowKind::Return; ret.callSite = callSite; ret.objects = objects;
      pending.push_back(ret);
    }
  }
  if (completeTargets)
    for (auto eid : g.outgoing(before)) {
      const auto &e = g.edge(eid);
      if (e.enabled && e.to == after && e.kind == FlowKind::History) g.disableEdge(eid);
    }
  for (const auto &e : pending) g.addEdge(e);
  if (!completeTargets) g.addIssue("unresolved temporal call target or external effect");
}
} // namespace
void TemporalHistory::connectCall(TraceFlowGraph &g,
                                  const TemporalHistory &caller, SiteID site,
                                  CallSiteID callSite,
                                  const std::vector<TemporalHistory> &targets,
                                  bool completeTargets, ObjectSet objects) {
  llvm::SmallVector<const TemporalHistory *, 4> references;
  for (const auto &target : targets)
    references.push_back(&target);
  connectTargets(g, caller, site, callSite, references, completeTargets,
                 std::move(objects));
}
void TemporalHistory::connectCallTargets(
    TraceFlowGraph &g, const TemporalHistory &caller, SiteID site,
    CallSiteID callSite, const std::vector<const TemporalHistory *> &targets,
    bool completeTargets, ObjectSet objects) {
  connectTargets(g, caller, site, callSite, targets, completeTargets,
                 std::move(objects));
}
void TemporalHistory::connectDirectCall(TraceFlowGraph &g,
                                        const TemporalHistory &caller,
                                        SiteID site, CallSiteID callSite,
                                        const TemporalHistory &target,
                                        bool completeTargets,
                                        ObjectSet objects) {
  const TemporalHistory *reference = &target;
  connectTargets(g, caller, site, callSite, {reference}, completeTargets,
                 std::move(objects));
}
} // namespace usetracessa
} // namespace lotus
