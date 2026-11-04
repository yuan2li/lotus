#ifndef LOTUS_IR_USETRACESSA_PATHFEASIBILITY_H
#define LOTUS_IR_USETRACESSA_PATHFEASIBILITY_H

#include "IR/UseTraceSSA/Query.h"
#include "IR/UseTraceSSA/SVFGBridge.h"

#include <cstddef>
#include <memory>

namespace lotus {
namespace usetracessa {

enum class Feasibility { Feasible, Infeasible, Unknown };

struct PathFeasibilityOptions {
  /// Z3 budget per witness; an exhausted budget yields Unknown.
  unsigned timeoutMilliseconds = 1000;
  /// Segments whose CFG slice exceeds this many blocks stay unconstrained.
  std::size_t maxSliceBlocks = 20000;
};

struct PathFeasibilityStatistics {
  std::size_t witnesses = 0, feasible = 0, infeasible = 0, unknown = 0;
  std::size_t segments = 0, unconstrainedSegments = 0, unlocatedNodes = 0;
};

/// Saber-style path-feasibility filter for UseTraceSSA witnesses, applied
/// after the search; the solver and its findings are unchanged.
///
/// Only the witness's automaton transitions (and its first and last node) are
/// kept: the history merges paths that are equivalent for the object, so the
/// nodes between transitions are an arbitrary choice. Transitions are grouped
/// into activation frames at call and return edges; each frame starts at its
/// function's entry. Consecutive transitions of a frame must be connected by
/// a CFG path that avoids every Must effect on the object that would change
/// the automaton's current state (e.g. a Must release before a leak's exit).
/// The path condition is the disjunction over such paths of their branch
/// literals, computed on the SCC condensation of the slice between the two
/// points, so branches inside loops stay unconstrained. Literals are shared
/// per frame: `x == C` and `x != C` (C an integer or null constant) constrain
/// one "x equals C" variable, other branches their i1 condition. An
/// allocation returns non-null in its frame. Callees between two transitions
/// are not inspected, which only admits more paths.
///
/// Infeasible means that this witness's transitions have no execution under
/// that model; the search retains one witness per sink and object.
class PathFeasibility {
public:
  PathFeasibility(const TraceFlowGraph &graph, const NativeLocations &locations,
                  Automaton automaton, PathFeasibilityOptions options = {});
  ~PathFeasibility();
  PathFeasibility(const PathFeasibility &) = delete;
  PathFeasibility &operator=(const PathFeasibility &) = delete;

  /// `witness` is a found witness of `object`, with its automaton states.
  Feasibility check(const QueryResult &witness, ObjectID object);
  const PathFeasibilityStatistics &statistics() const;

private:
  struct Impl;
  std::unique_ptr<Impl> State;
};

} // namespace usetracessa
} // namespace lotus
#endif
