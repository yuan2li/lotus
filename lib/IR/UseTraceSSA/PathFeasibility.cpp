#include "IR/UseTraceSSA/PathFeasibility.h"

#include <algorithm>
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <tuple>
#include <unordered_set>
#include <utility>
#include <vector>

#include <llvm/IR/CFG.h>
#include <llvm/IR/Constants.h>
#include <llvm/IR/InstrTypes.h>
#include <llvm/IR/Instructions.h>

#include <z3++.h>

namespace lotus {
namespace usetracessa {

struct PathFeasibility::Impl {
  const TraceFlowGraph &Graph;
  const NativeLocations &Locations;
  PathFeasibilityOptions Options;
  PathFeasibilityStatistics Statistics;
  z3::context Context;
  // Literals per activation frame (numbered within each witness): "value
  // equals constant" (null for every pointer type) and opaque i1 conditions.
  // Names are fixed by frame and value, so expressions are shared across
  // witnesses.
  std::map<std::tuple<unsigned, const llvm::Value *, const llvm::Constant *>, z3::expr>
      EqualVariables;
  std::map<std::tuple<unsigned, const llvm::Value *, const llvm::Constant *>, z3::expr>
      ConditionVariables;

  Impl(const TraceFlowGraph &graph, const NativeLocations &locations,
       Automaton automaton, PathFeasibilityOptions options)
      : Graph(graph), Locations(locations), Options(options),
        Machine(std::move(automaton)) {
    for (const auto &[node, point] : locations.nodes) {
      const auto &flow = graph.node(node);
      if (flow.events != Event::None || !flow.effects.empty())
        Effects[point.block->getParent()].push_back({point, node});
    }
  }

  z3::expr variable(
      std::map<std::tuple<unsigned, const llvm::Value *, const llvm::Constant *>,
               z3::expr> &table,
      const char *prefix, unsigned frame, const llvm::Value *value,
      const llvm::Constant *constant = nullptr) {
    auto key = std::make_tuple(frame, value, constant);
    auto found = table.find(key);
    if (found != table.end()) return found->second;
    std::string name = std::string(prefix) + std::to_string(frame) + "_" +
                       std::to_string(reinterpret_cast<std::uintptr_t>(value)) +
                       "_" + std::to_string(reinterpret_cast<std::uintptr_t>(constant));
    return table.emplace(key, Context.bool_const(name.c_str())).first->second;
  }

  /// "value == constant"; pointers compare through casts and every null
  /// pointer constant is the same constant.
  z3::expr equals(unsigned frame, const llvm::Value *value,
                  const llvm::Constant *constant) {
    if (value->getType()->isPointerTy()) {
      value = value->stripPointerCasts();
      if (constant->isNullValue()) constant = nullptr;
    }
    return variable(EqualVariables, "eq", frame, value, constant);
  }

  z3::expr isNull(unsigned frame, const llvm::Value *pointer) {
    return equals(frame, pointer,
                  llvm::Constant::getNullValue(pointer->getType()));
  }

  /// The condition under which a branch on `condition` takes its true
  /// (`positive`) or false successor.
  z3::expr literal(unsigned frame, const llvm::Value *condition, bool positive) {
    if (const auto *constant = llvm::dyn_cast<llvm::ConstantInt>(condition))
      return Context.bool_val(constant->isOne() == positive);
    if (const auto *binary = llvm::dyn_cast<llvm::BinaryOperator>(condition))
      if (binary->getOpcode() == llvm::Instruction::Xor)
        for (unsigned operand = 0; operand < 2; ++operand)
          if (const auto *one =
                  llvm::dyn_cast<llvm::ConstantInt>(binary->getOperand(operand)))
            if (one->isOne())
              return literal(frame, binary->getOperand(1 - operand), !positive);
    if (const auto *compare = llvm::dyn_cast<llvm::ICmpInst>(condition))
      if (compare->isEquality())
        for (unsigned operand = 0; operand < 2; ++operand) {
          const auto *other = compare->getOperand(1 - operand);
          const auto *constant =
              llvm::dyn_cast<llvm::Constant>(compare->getOperand(operand));
          if (!constant || llvm::isa<llvm::Constant>(other) ||
              !(llvm::isa<llvm::ConstantInt>(constant) || constant->isNullValue()))
            continue;
          z3::expr equal = equals(frame, other, constant);
          if (compare->getPredicate() == llvm::CmpInst::ICMP_NE) equal = !equal;
          return positive ? equal : !equal;
        }
    z3::expr value = variable(ConditionVariables, "cond", frame, condition);
    return positive ? value : !value;
  }

  z3::expr edgeLiteral(unsigned frame, const llvm::BasicBlock *block,
                       unsigned successor) {
    const auto *branch = llvm::dyn_cast<llvm::BranchInst>(block->getTerminator());
    if (!branch || !branch->isConditional()) return Context.bool_val(true);
    return literal(frame, branch->getCondition(), successor == 0);
  }

  /// Effect positions (see NativePoint: an effect happens just before its
  /// node's point) that a path segment must not pass, per block.
  using Blocked = std::unordered_map<const llvm::BasicBlock *, std::vector<unsigned>>;

  static bool anyIn(const Blocked &blocked, const llvm::BasicBlock *block,
                    unsigned low, unsigned high) { // [low, high]
    auto found = blocked.find(block);
    if (found == blocked.end()) return false;
    return std::any_of(found->second.begin(), found->second.end(),
                       [&](unsigned at) { return low <= at && at <= high; });
  }

  /// Blocks entered after leaving `from` (not passing fully blocked blocks,
  /// except arriving at `target`).
  std::unordered_set<const llvm::BasicBlock *>
  forwardFrom(const llvm::BasicBlock *from, const llvm::BasicBlock *target,
              const Blocked *blocked, bool &tooLarge) {
    std::unordered_set<const llvm::BasicBlock *> reached;
    std::vector<const llvm::BasicBlock *> work(llvm::succ_begin(from),
                                               llvm::succ_end(from));
    while (!work.empty()) {
      const auto *block = work.back();
      work.pop_back();
      if (reached.count(block)) continue;
      bool passable = !blocked || !blocked->count(block);
      if (!passable && block != target) continue;
      reached.insert(block);
      if (reached.size() > Options.maxSliceBlocks) {
        tooLarge = true;
        return reached;
      }
      if (passable)
        work.insert(work.end(), llvm::succ_begin(block), llvm::succ_end(block));
    }
    return reached;
  }

  /// Condition of reaching `to` from `from` inside one activation without
  /// passing a blocked effect.
  z3::expr guard(unsigned frame, NativePoint from, NativePoint to,
                 const Blocked &blocked) {
    ++Statistics.segments;
    auto unconstrained = [&] {
      ++Statistics.unconstrainedSegments;
      return Context.bool_val(true);
    };
    if (from.block == to.block && from.index <= to.index &&
        (to.index == 0 || !anyIn(blocked, from.block, from.index + 1, to.index - 1)))
      return Context.bool_val(true);
    const bool leaves =
        !anyIn(blocked, from.block, from.index + 1, unsigned(from.block->size()));
    const bool enters = to.index == 0 || !anyIn(blocked, to.block, 0, to.index - 1);
    bool tooLarge = false;
    auto forward = forwardFrom(from.block, to.block, &blocked, tooLarge);
    if (tooLarge) return unconstrained();
    if (!leaves || !enters || !forward.count(to.block)) {
      // Unreachable even ignoring the effects: the history and the CFG
      // disagree, so claim nothing.
      auto plain = forwardFrom(from.block, to.block, nullptr, tooLarge);
      if (tooLarge || !plain.count(to.block)) return unconstrained();
      return Context.bool_val(false);
    }
    // Slice: forward blocks that can still reach `to`.
    std::vector<const llvm::BasicBlock *> slice;
    std::unordered_map<const llvm::BasicBlock *, unsigned> index;
    std::vector<const llvm::BasicBlock *> work(1, to.block);
    while (!work.empty()) {
      const auto *block = work.back();
      work.pop_back();
      if (!forward.count(block) || index.count(block)) continue;
      index.emplace(block, slice.size());
      slice.push_back(block);
      work.insert(work.end(), llvm::pred_begin(block), llvm::pred_end(block));
    }
    struct Step {
      unsigned to;
      const llvm::BasicBlock *from;
      unsigned successor;
    };
    std::vector<std::vector<Step>> out(slice.size());
    for (unsigned node = 0; node < slice.size(); ++node) {
      if (slice[node] == to.block && blocked.count(to.block)) continue;
      const auto *terminator = slice[node]->getTerminator();
      for (unsigned successor = 0; successor < terminator->getNumSuccessors(); ++successor) {
        auto target = index.find(terminator->getSuccessor(successor));
        if (target != index.end())
          out[node].push_back({target->second, slice[node], successor});
      }
    }
    // Tarjan with an explicit stack; components come out in reverse
    // topological order.
    const unsigned none = ~0u;
    std::vector<unsigned> order(slice.size(), none), low(slice.size()),
        component(slice.size(), none);
    std::vector<unsigned> stack;
    std::vector<std::pair<unsigned, unsigned>> calls;
    unsigned counter = 0, components = 0;
    for (unsigned root = 0; root < slice.size(); ++root) {
      if (order[root] != none) continue;
      calls.push_back({root, 0});
      order[root] = low[root] = counter++;
      stack.push_back(root);
      while (!calls.empty()) {
        auto &[node, next] = calls.back();
        if (next < out[node].size()) {
          unsigned target = out[node][next++].to;
          if (order[target] == none) {
            order[target] = low[target] = counter++;
            stack.push_back(target);
            calls.push_back({target, 0});
          } else if (component[target] == none) {
            low[node] = std::min(low[node], order[target]);
          }
          continue;
        }
        unsigned finished = node;
        calls.pop_back();
        if (!calls.empty())
          low[calls.back().first] = std::min(low[calls.back().first], low[finished]);
        if (low[finished] != order[finished]) continue;
        unsigned member;
        do {
          member = stack.back();
          stack.pop_back();
          component[member] = components;
        } while (member != finished);
        ++components;
      }
    }
    // Reached-condition per component; edges inside a component (loops) are
    // unconstrained.
    std::vector<z3::expr> reached(components, Context.bool_val(false));
    std::vector<std::vector<unsigned>> members(components);
    for (unsigned node = 0; node < slice.size(); ++node)
      members[component[node]].push_back(node);
    const auto *terminator = from.block->getTerminator();
    for (unsigned successor = 0; successor < terminator->getNumSuccessors(); ++successor) {
      auto target = index.find(terminator->getSuccessor(successor));
      if (target != index.end())
        reached[component[target->second]] =
            reached[component[target->second]] ||
            edgeLiteral(frame, from.block, successor);
    }
    for (unsigned current = components; current-- > 0;)
      for (unsigned node : members[current])
        for (const auto &step : out[node])
          if (component[step.to] != current)
            reached[component[step.to]] =
                reached[component[step.to]] ||
                (reached[current] && edgeLiteral(frame, step.from, step.successor));
    return reached[component[index.at(to.block)]];
  }

  /// Segment conditions depend only on the frame number, the endpoints and
  /// the blocked set (cached, so identified by address).
  std::map<std::tuple<unsigned, const llvm::BasicBlock *, unsigned,
                      const llvm::BasicBlock *, unsigned, const Blocked *>,
           z3::expr>
      Guards;
  z3::expr cachedGuard(unsigned frame, NativePoint from, NativePoint to,
                       const Blocked &blocked) {
    auto key = std::make_tuple(frame, from.block, from.index, to.block, to.index,
                               &blocked);
    auto found = Guards.find(key);
    if (found != Guards.end()) return found->second;
    return Guards.emplace(key, guard(frame, from, to, blocked)).first->second;
  }

  /// Located nodes with events or guarded effects, per function.
  std::unordered_map<const llvm::Function *,
                     std::vector<std::pair<NativePoint, FlowNodeID>>> Effects;

  /// Must effects on `object` that would move the automaton out of `state`.
  std::map<std::tuple<const llvm::Function *, ID, ObjectID>, Blocked> Kills;
  const Blocked &kills(const llvm::Function *function, ID state, ObjectID object) {
    auto key = std::make_tuple(function, state, object);
    auto cached = Kills.find(key);
    if (cached != Kills.end()) return cached->second;
    Blocked &result = Kills[key];
    auto found = Effects.find(function);
    if (found == Effects.end()) return result;
    for (const auto &[point, node] : found->second) {
      auto effective = Graph.effectiveEvent(node, object);
      if (effective.events == Event::None || effective.certainty != Certainty::Must)
        continue;
      if (Machine.transition(state, effective.events) != state)
        result[point.block].push_back(point.index);
    }
    return result;
  }

  Automaton Machine;

  static bool allocates(const FlowNode &node) {
    if (hasEvent(node.events, Event::Allocate)) return true;
    return std::any_of(node.effects.begin(), node.effects.end(),
                       [](const GuardedEventEffect &effect) {
                         return hasEvent(effect.events, Event::Allocate);
                       });
  }

  Feasibility check(const QueryResult &witness, ObjectID object) {
    ++Statistics.witnesses;
    if (witness.automatonStates.size() != witness.nodes.size()) {
      ++Statistics.unknown;
      return Feasibility::Unknown;
    }
    z3::solver solver(Context);
    z3::params parameters(Context);
    parameters.set("timeout", Options.timeoutMilliseconds);
    solver.set(parameters);
    struct Frame {
      unsigned id;
      std::optional<NativePoint> last;
      /// Automaton state since `last`; unset before the witness's first
      /// point in the frame, where earlier effects are unknown.
      std::optional<ID> state;
      bool fresh = true;
    };
    unsigned frames = 0;
    std::vector<Frame> stack{{frames++, std::nullopt, std::nullopt}};
    auto advance = [&](Frame &frame, NativePoint point, std::optional<ID> state) {
      const auto *function = point.block->getParent();
      if (!frame.last || frame.last->block->getParent() != function) {
        frame.last = NativePoint{&function->getEntryBlock(), 0};
        frame.state.reset();
      }
      static const Blocked none;
      const Blocked &blocked =
          frame.state ? kills(function, *frame.state, object) : none;
      solver.add(cachedGuard(frame.id, *frame.last, point, blocked));
      frame.last = point;
      frame.state = state;
    };
    for (std::size_t step = 0; step < witness.nodes.size(); ++step) {
      bool boundary = step == 0 || step + 1 == witness.nodes.size();
      if (step > 0 && step - 1 < witness.edges.size()) {
        auto kind = Graph.edge(witness.edges[step - 1]).kind;
        if (kind == FlowKind::Call) {
          stack.push_back({frames++, std::nullopt, std::nullopt});
          boundary = true;
        } else if (kind == FlowKind::Return) {
          // An unmatched return (a realizable witness may start in a callee)
          // continues in a caller activation the witness has not seen.
          if (stack.size() > 1) stack.pop_back();
          else stack.back() = {frames++, std::nullopt, std::nullopt};
          boundary = true;
        }
      }
      const ID state = witness.automatonStates[step];
      const bool transition = step > 0 && state != witness.automatonStates[step - 1];
      if (!boundary && !transition) continue;
      auto located = Locations.nodes.find(witness.nodes[step]);
      if (located == Locations.nodes.end()) {
        ++Statistics.unlocatedNodes;
        continue;
      }
      const NativePoint point = located->second;
      Frame &frame = stack.back();
      // A callee's activation is entered in the caller's current state.
      std::optional<ID> entered;
      if (frame.fresh && step > 0) entered = witness.automatonStates[step - 1];
      if (frame.fresh && entered && point.index == 0 &&
          point.block == &point.block->getParent()->getEntryBlock()) {
        frame.last = point;
        frame.state = entered;
      }
      frame.fresh = false;
      advance(frame, point, state);
      if (allocates(Graph.node(witness.nodes[step])) && point.index > 0) {
        auto instruction = point.block->begin();
        std::advance(instruction, point.index - 1);
        if (const auto *call = llvm::dyn_cast<llvm::CallBase>(&*instruction))
          if (call->getType()->isPointerTy())
            solver.add(!isNull(frame.id, call));
      }
    }
    switch (solver.check()) {
    case z3::sat:
      ++Statistics.feasible;
      return Feasibility::Feasible;
    case z3::unsat:
      ++Statistics.infeasible;
      return Feasibility::Infeasible;
    default:
      ++Statistics.unknown;
      return Feasibility::Unknown;
    }
  }
};

PathFeasibility::PathFeasibility(const TraceFlowGraph &graph,
                                 const NativeLocations &locations,
                                 Automaton automaton,
                                 PathFeasibilityOptions options)
    : State(std::make_unique<Impl>(graph, locations, std::move(automaton), options)) {}

PathFeasibility::~PathFeasibility() = default;

Feasibility PathFeasibility::check(const QueryResult &witness, ObjectID object) {
  return State->check(witness, object);
}

const PathFeasibilityStatistics &PathFeasibility::statistics() const {
  return State->Statistics;
}

} // namespace usetracessa
} // namespace lotus
