//===- DOD.h - Decisive-order dependence ----------------------*- C++ -*-===//

#pragma once

#include "Analysis/ControlDependence/ControlDependenceGraph.h"

#include <functional>

namespace lotus::cd::detail {

DependenceResult computeDOD(Graph &graph);
DependenceResult computeDODRanganath(Graph &graph);
DependenceResult computeDODNTSCD(Graph &graph);

/// Execute baseline DOD preprocessing through projection construction and
/// range-boundary discovery, stopping before endpoint-pair traversal.
size_t preprocessBaselineDOD(Graph &graph);

/// Chalupa et al.'s NTSCD and DOD closure (CAV'21, Definition 10): both
/// relations are materialized from one shared all-max-paths computation and
/// the seed is closed by backward reachability over NTSCD edges and DOD
/// hyperedges. For a start-rooted seed on a graph whose vertices are all
/// reachable from start, this is the strong control closure. Pass
/// includeDOD = false only when DOD is known to be empty.
NodeSet computeBaselineDependencyClosure(Graph &graph, const NodeSet &seed,
                                         bool includeDOD = true);

/// Run the baseline DOD construction and stream exact triples without storing
/// them. This preserves the baseline algorithm while making output-sensitive
/// experiments comparable with compact biclique enumeration.
void forEachBaselineDODPair(
    Graph &graph,
    const std::function<void(GraphNode *decision, GraphNode *first,
                             GraphNode *second)> &callback);

} // namespace lotus::cd::detail
