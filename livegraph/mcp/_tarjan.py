"""Tarjan's strongly-connected components algorithm.

Used by `find_cycles` to identify SCCs in the call/import graph
without depending on networkx, the Neo4j GDS library, or APOC.

The input is a plain adjacency-list dict: ``{node: [neighbors...]}``.
Nodes appearing only as targets are auto-added as singletons. Returns
a list of components; each component is a list of nodes.

Implementation: iterative Tarjan (no recursion — Python's recursion
limit would otherwise bite us on big graphs).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence


def strongly_connected_components(
    graph: Mapping[str, Sequence[str]],
) -> list[list[str]]:
    """Iterative Tarjan's SCC. Returns components, no guaranteed order."""
    nodes: set[str] = set(graph.keys())
    for adj in graph.values():
        nodes.update(adj)
    if not nodes:
        return []

    index_counter = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    sccs: list[list[str]] = []

    for start in nodes:
        if start in indices:
            continue
        work: list[tuple[str, list[str], int]] = [
            (start, list(graph.get(start, ())), 0),
        ]
        indices[start] = index_counter
        lowlinks[start] = index_counter
        index_counter += 1
        stack.append(start)
        on_stack.add(start)

        while work:
            node, neighbors, ni = work[-1]
            if ni < len(neighbors):
                w = neighbors[ni]
                work[-1] = (node, neighbors, ni + 1)
                if w not in indices:
                    indices[w] = index_counter
                    lowlinks[w] = index_counter
                    index_counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, list(graph.get(w, ())), 0))
                elif w in on_stack:
                    lowlinks[node] = min(lowlinks[node], indices[w])
            else:
                if lowlinks[node] == indices[node]:
                    component: list[str] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        component.append(w)
                        if w == node:
                            break
                    sccs.append(component)
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlinks[parent] = min(lowlinks[parent], lowlinks[node])

    return sccs
