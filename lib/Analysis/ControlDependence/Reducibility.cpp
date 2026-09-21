//===- Reducibility.cpp - Reducibility guard for DOD ----------------------===//

#include "Analysis/ControlDependence/Reducibility.h"

#include <algorithm>
#include <climits>
#include <utility>
#include <vector>

namespace lotus::cd::detail {

namespace {

enum : unsigned char { Unvisited, OnStack, Finished };

// Whether some binary decision in \p unreachable can reach a vertex on a
// cycle. Tarjan's algorithm marks vertices on a cycle (an SCC with more than
// one vertex, or a self-loop); a backward sweep from them marks every vertex
// that can reach one.
bool unreachableDecisionReachesCycle(const Graph &graph,
                                     const std::vector<unsigned char> &state) {
  const unsigned n = graph.size();
  std::vector<unsigned> index(n + 1, UINT_MAX), low(n + 1, 0);
  std::vector<bool> onTarjanStack(n + 1, false), onCycle(n + 1, false);
  std::vector<unsigned> tarjanStack;
  unsigned counter = 0;
  for (unsigned root = 1; root <= n; ++root) {
    if (index[root] != UINT_MAX)
      continue;
    std::vector<std::pair<unsigned, size_t>> calls{{root, 0}};
    index[root] = low[root] = counter++;
    tarjanStack.push_back(root);
    onTarjanStack[root] = true;
    while (!calls.empty()) {
      unsigned v = calls.back().first;
      size_t &next = calls.back().second;
      const auto &successors = graph.getNode(v)->successors();
      if (next < successors.size()) {
        unsigned w = successors[next++]->getID();
        if (index[w] == UINT_MAX) {
          index[w] = low[w] = counter++;
          tarjanStack.push_back(w);
          onTarjanStack[w] = true;
          calls.push_back({w, 0});
        } else if (onTarjanStack[w]) {
          low[v] = std::min(low[v], index[w]);
        }
        continue;
      }
      if (low[v] == index[v]) {
        std::vector<unsigned> component;
        unsigned w;
        do {
          w = tarjanStack.back();
          tarjanStack.pop_back();
          onTarjanStack[w] = false;
          component.push_back(w);
        } while (w != v);
        if (component.size() > 1) {
          for (unsigned member : component)
            onCycle[member] = true;
        } else {
          const auto &own = graph.getNode(v)->successors();
          onCycle[v] =
              std::find(own.begin(), own.end(), graph.getNode(v)) != own.end();
        }
      }
      calls.pop_back();
      if (!calls.empty()) {
        unsigned parent = calls.back().first;
        low[parent] = std::min(low[parent], low[v]);
      }
    }
  }

  std::vector<bool> reachesCycle(onCycle);
  std::vector<unsigned> worklist;
  for (unsigned v = 1; v <= n; ++v)
    if (onCycle[v])
      worklist.push_back(v);
  while (!worklist.empty()) {
    unsigned v = worklist.back();
    worklist.pop_back();
    for (const GraphNode *pred : graph.getNode(v)->predecessors())
      if (!reachesCycle[pred->getID()]) {
        reachesCycle[pred->getID()] = true;
        worklist.push_back(pred->getID());
      }
  }

  for (unsigned v = 1; v <= n; ++v)
    if (state[v] == Unvisited && graph.getNode(v)->successors().size() == 2 &&
        reachesCycle[v])
      return true;
  return false;
}

} // namespace

bool isDODEmptyByReducibility(const Graph &graph, const GraphNode &start) {
  const unsigned n = graph.size();
  const unsigned root = start.getID();

  // Depth-first search from the start: postorder of the reachable vertices and
  // the retreating edges, whose targets are still on the search stack.
  std::vector<unsigned char> state(n + 1, Unvisited);
  std::vector<unsigned> postorder;
  postorder.reserve(n);
  std::vector<std::pair<unsigned, unsigned>> retreating;
  std::vector<std::pair<const GraphNode *, size_t>> stack{{&start, 0}};
  state[root] = OnStack;
  while (!stack.empty()) {
    const GraphNode *node = stack.back().first;
    size_t &next = stack.back().second;
    if (next < node->successors().size()) {
      const GraphNode *successor = node->successors()[next++];
      unsigned id = successor->getID();
      if (state[id] == Unvisited) {
        state[id] = OnStack;
        stack.push_back({successor, 0});
      } else if (state[id] == OnStack) {
        retreating.push_back({node->getID(), id});
      }
      continue;
    }
    state[node->getID()] = Finished;
    postorder.push_back(node->getID());
    stack.pop_back();
  }

  // Immediate dominators of the reachable vertices (Cooper, Harvey, and
  // Kennedy, "A Simple, Fast Dominance Algorithm").
  std::vector<unsigned> rpoIndex(n + 1, UINT_MAX);
  std::vector<unsigned> rpo(postorder.rbegin(), postorder.rend());
  for (unsigned i = 0; i < rpo.size(); ++i)
    rpoIndex[rpo[i]] = i;
  std::vector<unsigned> idom(n + 1, 0);
  idom[root] = root;
  auto intersect = [&](unsigned a, unsigned b) {
    while (a != b) {
      while (rpoIndex[a] > rpoIndex[b])
        a = idom[a];
      while (rpoIndex[b] > rpoIndex[a])
        b = idom[b];
    }
    return a;
  };
  for (bool changed = true; changed;) {
    changed = false;
    for (unsigned i = 1; i < rpo.size(); ++i) {
      unsigned v = rpo[i];
      unsigned candidate = 0;
      for (const GraphNode *pred : graph.getNode(v)->predecessors()) {
        unsigned u = pred->getID();
        if (state[u] != Finished || idom[u] == 0)
          continue;
        candidate = candidate ? intersect(u, candidate) : u;
      }
      if (idom[v] != candidate) {
        idom[v] = candidate;
        changed = true;
      }
    }
  }

  // The reachable part is reducible iff every retreating edge u -> v is a back
  // edge, that is, v dominates u. Answer dominance with entry/exit times on the
  // dominator tree.
  if (!retreating.empty()) {
    std::vector<std::vector<unsigned>> children(n + 1);
    for (unsigned v : rpo)
      if (v != root)
        children[idom[v]].push_back(v);
    std::vector<unsigned> enter(n + 1, 0), leave(n + 1, 0);
    unsigned clock = 0;
    std::vector<std::pair<unsigned, size_t>> walk{{root, 0}};
    enter[root] = clock++;
    while (!walk.empty()) {
      unsigned v = walk.back().first;
      size_t &next = walk.back().second;
      if (next < children[v].size()) {
        unsigned child = children[v][next++];
        enter[child] = clock++;
        walk.push_back({child, 0});
        continue;
      }
      leave[v] = clock++;
      walk.pop_back();
    }
    for (auto [tail, head] : retreating)
      if (!(enter[head] <= enter[tail] && leave[tail] <= leave[head]))
        return false;
  }

  if (postorder.size() == n)
    return true;
  return !unreachableDecisionReachesCycle(graph, state);
}

} // namespace lotus::cd::detail
