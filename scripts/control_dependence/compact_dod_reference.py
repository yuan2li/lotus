#!/usr/bin/env python3
"""Reference implementation for compact strong control dependencies.

The implementation follows the definitions and theorems in the accompanying
technical report. Vertices are integers 0,...,n-1. Edges are sets: parallel
edges are not represented. The graph may have arbitrary out-degree. DOD is
computed for vertices with exactly two distinct successors.

This code favors clarity and executable checking over low-level optimization.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from itertools import combinations
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple


EMPTY = -1
MANY = -2


def _join_cap(a: int, b: int) -> int:
    """Join capped sets encoded as EMPTY, MANY, or a singleton vertex id."""
    if a == MANY or b == MANY:
        return MANY
    if a == EMPTY:
        return b
    if b == EMPTY:
        return a
    return a if a == b else MANY


@dataclass(frozen=True)
class CompactDOD:
    """One complete bipartite DOD relation for a decision vertex.

    For the associated decision p, the exact unordered DOD pairs are
    {{a,b} | a in left and b in right}. The two sides are disjoint.
    """

    left: frozenset[int]
    right: frozenset[int]
    cycle: Tuple[int, ...]
    branch1_entries: frozenset[int]
    branch2_entries: frozenset[int]

    def contains(self, a: int, b: int) -> bool:
        return ((a in self.left and b in self.right) or
                (a in self.right and b in self.left))

    def pairs(self) -> Iterator[Tuple[int, int]]:
        for a in sorted(self.left):
            for b in sorted(self.right):
                yield (a, b) if a < b else (b, a)


class DiGraph:
    def __init__(self, adjacency: Sequence[Iterable[int]]):
        self.n = len(adjacency)
        normalized: List[Tuple[int, ...]] = []
        for u, succs in enumerate(adjacency):
            row = tuple(sorted(set(succs)))
            for v in row:
                if v < 0 or v >= self.n:
                    raise ValueError(f"edge {u}->{v} is outside 0..{self.n - 1}")
            normalized.append(row)
        self.succ: Tuple[Tuple[int, ...], ...] = tuple(normalized)
        pred: List[List[int]] = [[] for _ in range(self.n)]
        for u, row in enumerate(self.succ):
            for v in row:
                pred[v].append(u)
        self.pred: Tuple[Tuple[int, ...], ...] = tuple(tuple(xs) for xs in pred)
        self.m = sum(len(row) for row in self.succ)

    @property
    def binary_decisions(self) -> Tuple[int, ...]:
        return tuple(v for v in range(self.n) if len(self.succ[v]) == 2)

    def inevitable_rows(self) -> Tuple[int, ...]:
        """Return row bitsets I[v] = {t | every maximal v-path contains t}.

        Time: O(n(n+m)); space: O(n^2) bits plus O(n+m) working memory.
        """
        rows = [0] * self.n
        outdegree = [len(row) for row in self.succ]

        for target in range(self.n):
            marked = [False] * self.n
            remaining = outdegree.copy()
            work = deque([target])
            marked[target] = True
            rows[target] |= 1 << target

            while work:
                x = work.popleft()
                for v in self.pred[x]:
                    if marked[v]:
                        continue
                    remaining[v] -= 1
                    if remaining[v] == 0 and outdegree[v] > 0:
                        marked[v] = True
                        rows[v] |= 1 << target
                        work.append(v)

        return tuple(rows)

    def ntscd(self, inevitable_rows: Optional[Sequence[int]] = None,
              decisions: Optional[Iterable[int]] = None) -> Set[Tuple[int, int]]:
        """Compute multiway NTSCD: a proper nonempty subset of branches must hit t.

        On binary decisions this is exactly the usual NTSCD definition.
        """
        rows = tuple(inevitable_rows) if inevitable_rows is not None else self.inevitable_rows()
        ds = tuple(decisions) if decisions is not None else tuple(
            v for v in range(self.n) if len(self.succ[v]) >= 2
        )
        result: Set[Tuple[int, int]] = set()
        for p in ds:
            d = len(self.succ[p])
            if d < 2:
                continue
            for target in range(self.n):
                must_count = sum((rows[s] >> target) & 1 for s in self.succ[p])
                if 0 < must_count < d:
                    result.add((p, target))
        return result

    def _first_hits(self, start: int, in_s: Sequence[bool]) -> Set[int]:
        if in_s[start]:
            return {start}
        hits: Set[int] = set()
        seen = [False] * self.n
        seen[start] = True
        stack = [start]
        while stack:
            x = stack.pop()
            for y in self.succ[x]:
                if in_s[y]:
                    hits.add(y)
                elif not seen[y]:
                    seen[y] = True
                    stack.append(y)
        return hits

    def _outside_sccs(self, in_s: Sequence[bool]) -> Tuple[List[int], int, List[Set[int]], List[int]]:
        r"""Kosaraju SCCs of G[V\S], plus condensation and capped direct-S labels."""
        outside = [v for v in range(self.n) if not in_s[v]]
        seen = [False] * self.n
        finish: List[int] = []

        for root in outside:
            if seen[root]:
                continue
            seen[root] = True
            stack: List[Tuple[int, int]] = [(root, 0)]
            while stack:
                x, i = stack[-1]
                row = self.succ[x]
                while i < len(row) and in_s[row[i]]:
                    i += 1
                if i < len(row):
                    y = row[i]
                    stack[-1] = (x, i + 1)
                    if not seen[y]:
                        seen[y] = True
                        stack.append((y, 0))
                else:
                    stack.pop()
                    finish.append(x)

        comp = [-1] * self.n
        count = 0
        for root in reversed(finish):
            if comp[root] != -1:
                continue
            comp[root] = count
            stack = [root]
            while stack:
                x = stack.pop()
                for y in self.pred[x]:
                    if in_s[y] or comp[y] != -1:
                        continue
                    comp[y] = count
                    stack.append(y)
            count += 1

        dag: List[Set[int]] = [set() for _ in range(count)]
        direct = [EMPTY] * count
        for x in outside:
            cx = comp[x]
            for y in self.succ[x]:
                if in_s[y]:
                    direct[cx] = _join_cap(direct[cx], y)
                else:
                    cy = comp[y]
                    if cx != cy:
                        dag[cx].add(cy)
        return comp, count, dag, direct

    def _capped_first_hit_labels(self, in_s: Sequence[bool]) -> Tuple[List[int], List[int]]:
        """Return SCC ids and cap(First_S(x)) for each outside SCC."""
        comp, count, dag, labels = self._outside_sccs(in_s)
        indegree = [0] * count
        for c in range(count):
            for d in dag[c]:
                indegree[d] += 1
        queue = deque(c for c in range(count) if indegree[c] == 0)
        topo: List[int] = []
        while queue:
            c = queue.popleft()
            topo.append(c)
            for d in dag[c]:
                indegree[d] -= 1
                if indegree[d] == 0:
                    queue.append(d)
        if len(topo) != count:
            raise AssertionError("SCC condensation is not acyclic")
        for c in reversed(topo):
            value = labels[c]
            for d in dag[c]:
                value = _join_cap(value, labels[d])
            labels[c] = value
        return comp, labels

    def _cycle_successor_map(self, p: int, in_s: Sequence[bool]) -> Dict[int, int]:
        comp, labels = self._capped_first_hit_labels(in_s)
        sigma: Dict[int, int] = {}
        for x in range(self.n):
            if not in_s[x] or x == p:
                continue
            value = EMPTY
            for y in self.succ[x]:
                value = _join_cap(value, y if in_s[y] else labels[comp[y]])
            if value < 0:
                raise AssertionError(
                    f"projection successor of {x} is not unique (capped label {value})"
                )
            if value == p:
                raise AssertionError("projection cycle has an edge into the decision vertex")
            sigma[x] = value
        return sigma

    @staticmethod
    def _cycle_order(sigma: Dict[int, int]) -> Tuple[int, ...]:
        if not sigma:
            raise AssertionError("expected a nonempty projection cycle")
        start = min(sigma)
        order: List[int] = []
        seen: Set[int] = set()
        x = start
        while x not in seen:
            if x not in sigma:
                raise AssertionError("projection successor leaves the expected cycle")
            seen.add(x)
            order.append(x)
            x = sigma[x]
        if x != start or len(seen) != len(sigma):
            raise AssertionError("projection is not one directed simple cycle")
        return tuple(order)

    @staticmethod
    def _cyclic_interval(order: Sequence[int], start_index: int,
                         end_index: int) -> frozenset[int]:
        """[start,end) in the directed cyclic order."""
        q = len(order)
        result: List[int] = []
        i = start_index
        while i != end_index:
            result.append(order[i])
            i = (i + 1) % q
        return frozenset(result)

    def compact_dod_for(self, p: int, inevitable_rows: Sequence[int]) -> Optional[CompactDOD]:
        """Compute the exact compact DOD biclique for one binary decision."""
        if len(self.succ[p]) != 2:
            raise ValueError(f"vertex {p} does not have exactly two successors")

        s_bits = inevitable_rows[p]
        in_s = [bool((s_bits >> v) & 1) for v in range(self.n)]
        if not in_s[p]:
            raise AssertionError("a path from p contains p at its first position")
        # Every DOD endpoint lies in S_p - {p}, so a pair needs two vertices there.
        if sum(in_s) < 3:
            return None

        s1, s2 = self.succ[p]
        b1 = self._first_hits(s1, in_s)
        b2 = self._first_hits(s2, in_s)
        projection_successors = b1 | b2
        if len(projection_successors) <= 1:
            return None
        if p in projection_successors:
            raise AssertionError("a universal projection with branching cannot return to p")
        if b1 & b2:
            return None

        sigma = self._cycle_successor_map(p, in_s)
        order = self._cycle_order(sigma)
        cycle_set = set(order)
        expected = {v for v in range(self.n) if in_s[v] and v != p}
        if cycle_set != expected:
            raise AssertionError("projection cycle does not contain exactly S_p minus p")
        if not b1 or not b2 or not (b1 | b2) <= cycle_set:
            raise AssertionError("branch-entry sets do not lie on the projection cycle")

        marked: List[Tuple[int, int]] = []
        for i, x in enumerate(order):
            if x in b1:
                marked.append((i, 1))
            elif x in b2:
                marked.append((i, 2))
        if not marked:
            raise AssertionError("both branch-entry sets are nonempty")

        transitions: List[Tuple[Tuple[int, int], Tuple[int, int]]] = []
        for j, current in enumerate(marked):
            nxt = marked[(j + 1) % len(marked)]
            if current[1] != nxt[1]:
                transitions.append((current, nxt))
        if len(transitions) != 2:
            return None

        alpha = beta = gamma = delta = None
        for (i, lab), (j, next_lab) in transitions:
            if lab == 1 and next_lab == 2:
                alpha, beta = i, j
            elif lab == 2 and next_lab == 1:
                gamma, delta = i, j
        if None in (alpha, beta, gamma, delta):
            raise AssertionError("two-label cycle must have both transition directions")

        left = self._cyclic_interval(order, int(alpha), int(beta))
        right = self._cyclic_interval(order, int(gamma), int(delta))
        if not left or not right or left & right:
            raise AssertionError("DOD biclique sides must be nonempty and disjoint")
        return CompactDOD(
            left=left,
            right=right,
            cycle=order,
            branch1_entries=frozenset(b1),
            branch2_entries=frozenset(b2),
        )

    def compact_dod(self, inevitable_rows: Optional[Sequence[int]] = None,
                    decisions: Optional[Iterable[int]] = None) -> Dict[int, CompactDOD]:
        rows = tuple(inevitable_rows) if inevitable_rows is not None else self.inevitable_rows()
        ds = tuple(decisions) if decisions is not None else self.binary_decisions
        result: Dict[int, CompactDOD] = {}
        for p in ds:
            representation = self.compact_dod_for(p, rows)
            if representation is not None:
                result[p] = representation
        return result

    @staticmethod
    def explicit_dod(compact: Dict[int, CompactDOD]) -> Set[Tuple[int, int, int]]:
        """Return canonical triples (p,min(a,b),max(a,b))."""
        result: Set[Tuple[int, int, int]] = set()
        for p, rep in compact.items():
            for a in rep.left:
                for b in rep.right:
                    result.add((p, min(a, b), max(a, b)))
        return result

    def rooted_dependency_closure(
        self,
        seed: Iterable[int],
        ntscd_relation: Set[Tuple[int, int]],
        compact_dod: Dict[int, CompactDOD],
    ) -> Set[int]:
        """Least set containing seed and closed under NTSCD and DOD."""
        ntscd_by_target: List[List[int]] = [[] for _ in range(self.n)]
        left_of: List[List[int]] = [[] for _ in range(self.n)]
        right_of: List[List[int]] = [[] for _ in range(self.n)]
        for p, target in ntscd_relation:
            ntscd_by_target[target].append(p)
        for p, rep in compact_dod.items():
            for x in rep.left:
                left_of[x].append(p)
            for x in rep.right:
                right_of[x].append(p)

        in_closure = [False] * self.n
        work: deque[int] = deque()
        for x in seed:
            if x < 0 or x >= self.n:
                raise ValueError(f"seed vertex {x} is outside 0..{self.n - 1}")
            if not in_closure[x]:
                in_closure[x] = True
                work.append(x)

        hit_left = [False] * self.n
        hit_right = [False] * self.n

        def add(x: int) -> None:
            if not in_closure[x]:
                in_closure[x] = True
                work.append(x)

        while work:
            x = work.popleft()
            for p in ntscd_by_target[x]:
                add(p)
            for p in left_of[x]:
                if not hit_left[p]:
                    hit_left[p] = True
                    if hit_right[p]:
                        add(p)
            for p in right_of[x]:
                if not hit_right[p]:
                    hit_right[p] = True
                    if hit_left[p]:
                        add(p)

        return {v for v, present in enumerate(in_closure) if present}

    # ---------- Validation helpers (definition-level, not used by algorithm) ----------

    def _reachable_before(self, start: int, target: int, forbidden: int) -> bool:
        """Whether target is reachable before any occurrence of forbidden."""
        if start == forbidden:
            return False
        if start == target:
            return True
        seen = [False] * self.n
        seen[start] = True
        stack = [start]
        while stack:
            x = stack.pop()
            for y in self.succ[x]:
                if y == forbidden:
                    continue
                if y == target:
                    return True
                if not seen[y]:
                    seen[y] = True
                    stack.append(y)
        return False

    def brute_dod(self, inevitable_rows: Optional[Sequence[int]] = None,
                  decisions: Optional[Iterable[int]] = None) -> Set[Tuple[int, int, int]]:
        """Definition-level DOD checker used for exhaustive regression tests."""
        rows = tuple(inevitable_rows) if inevitable_rows is not None else self.inevitable_rows()
        ds = tuple(decisions) if decisions is not None else self.binary_decisions
        result: Set[Tuple[int, int, int]] = set()
        for p in ds:
            s1, s2 = self.succ[p]
            for a, b in combinations((v for v in range(self.n) if v != p), 2):
                if not ((rows[p] >> a) & 1 and (rows[p] >> b) & 1):
                    continue
                a_before_b_1 = not self._reachable_before(s1, b, a)
                b_before_a_1 = not self._reachable_before(s1, a, b)
                a_before_b_2 = not self._reachable_before(s2, b, a)
                b_before_a_2 = not self._reachable_before(s2, a, b)
                if ((a_before_b_1 and b_before_a_2) or
                        (b_before_a_1 and a_before_b_2)):
                    result.add((p, a, b))
        return result


def _smoke_test() -> None:
    # A 4-cycle with a binary decision entering opposite points.
    graph = DiGraph([
        [1, 3],  # p = 0
        [2],
        [3],
        [4],
        [1],
    ])
    rows = graph.inevitable_rows()
    compact = graph.compact_dod(rows)
    explicit = graph.explicit_dod(compact)
    brute = graph.brute_dod(rows)
    assert explicit == brute, (explicit, brute)
    assert compact[0].left == frozenset({1, 2})
    assert compact[0].right == frozenset({3, 4})


if __name__ == "__main__":
    _smoke_test()
    print("compact_dod_reference.py: smoke test passed")
