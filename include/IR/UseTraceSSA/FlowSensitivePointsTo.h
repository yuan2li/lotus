#pragma once

#include "Alias/InclusionBased/FlowSensitive/Sparse/FlowSensitivePTA.h"
#include "IR/SVFG/SVFG.h"
#include "IR/SVFG/SVFGBuilder.h"

#include <algorithm>
#include <memory>
#include <optional>
#include <stdexcept>
#include <vector>

namespace lotus {
namespace usetracessa {
namespace cli {

/// Flow-sensitive points-to over an SVFG built with Memory SSA, for the
/// pointer-analysis ablation (NativeHistoryOptions::pointsTo). Objects keep
/// the SVFG's IDs; field-insensitive representatives created while solving
/// are reported as their base object. Indirect calls are bounded by the
/// builder's Andersen call graph, as in lotus-alias-fspta.
class FlowSensitivePointsTo {
public:
  FlowSensitivePointsTo(analysis::SVFG &graph, analysis::SVFGBuilder &builder) {
    alias::FlowSensitivePTA::Config config;
    config.isIndirectCallTargetAllowed = [&builder](const llvm::CallBase *call,
                                                    const llvm::Function *target) {
      const std::vector<const llvm::Function *> allowed =
          builder.getIndirectCallTargets(call);
      return std::find(allowed.begin(), allowed.end(), target) != allowed.end();
    };
    config.connectIndirectCall = [&graph, &builder, allowed = config.isIndirectCallTargetAllowed](
                                     const llvm::CallBase *call, const llvm::Function *target) {
      if (!allowed(call, target)) return false;
      std::vector<analysis::SVFGEdge *> edges;
      return builder.connectCallSiteToCalleeOnTheFly(&graph, call, target, edges);
    };
    Solver = std::make_unique<alias::FlowSensitivePTA>(graph, std::move(config));
    Solver->solve();
    if (Solver->timedOut())
      throw std::runtime_error("flow-sensitive points-to analysis timed out");
  }

  std::optional<analysis::SVFGNodeBS> operator()(const llvm::Value *value) const {
    auto objects = Solver->pointsTo(value);
    if (!objects) return std::nullopt;
    analysis::SVFGNodeBS canonical;
    for (auto object : *objects) canonical.insert(Solver->canonicalObject(object));
    return canonical;
  }

  const alias::FlowSensitivePTA::Statistics &statistics() const {
    return Solver->statistics();
  }

private:
  std::unique_ptr<alias::FlowSensitivePTA> Solver;
};

} // namespace cli
} // namespace usetracessa
} // namespace lotus
