//===- Reducibility.h - Reducibility guard for DOD --------------*- C++ -*-===//

#pragma once

#include "Analysis/ControlDependence/ControlDependenceGraph.h"

namespace lotus::cd::detail {

/// Whether DOD is known to be empty on \p graph without computing it.
///
/// The guard holds when the subgraph reachable from \p start is reducible and
/// no binary decision that \p start cannot reach can reach a cycle. DOD is
/// empty on reducible graphs; a DOD triple at a decision needs two vertices met
/// in opposite orders on its branches, which is impossible when everything the
/// decision reaches is acyclic. The second clause matters because clients such
/// as lotus-ir-control-dependence build a vertex for every basic block,
/// including blocks the entry cannot reach: an unreachable decision that enters
/// a reachable loop at two points has a non-empty DOD even though the reachable
/// part is reducible, so a check of the reachable part alone, such as LLVM's
/// containsIrreducibleCFG, is not sufficient.
///
/// The guard is sufficient, not necessary: irreducible graphs may still have an
/// empty DOD. It runs in near-linear time: one DFS, iterative dominators over
/// the reachable vertices, and, only when some vertex is unreachable, one SCC
/// pass and one backward sweep.
bool isDODEmptyByReducibility(const Graph &graph, const GraphNode &start);

} // namespace lotus::cd::detail
