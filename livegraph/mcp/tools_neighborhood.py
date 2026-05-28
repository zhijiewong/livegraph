"""semantic_neighborhood — vector seeds + per-seed graph expansion.

Lives in its own module so tools.py doesn't keep growing. The seed step
is shared with `semantic_search` via `_semantic_seeds` in `tools.py`.
"""
from __future__ import annotations

from typing import Any

from livegraph.graph.backend import GraphBackend
from livegraph.mcp.tools import _embedded_count, _semantic_seeds
from livegraph.semantic.provider import (
    EmbeddingExtraMissing, EmbeddingProvider,
)

_VALID_KINDS = ("any", "function", "method")
_VALID_INCLUDE = ("callers", "callees", "tests")
_MAX_LIMIT = 50
_MAX_PER_SEED = 50

_CALLERS_CYPHER = (
    "UNWIND $qns AS qn "
    "MATCH (target {qualified_name: qn}) "
    "MATCH (caller)-[r:CALLS]->(target) "
    "WITH qn, caller, collect(DISTINCT r.provenance) AS provs "
    "WITH qn, caller.qualified_name AS neighbor_qn, "
    "     CASE WHEN size(provs) > 1 THEN 'both' ELSE provs[0] END "
    "         AS provenance "
    "ORDER BY qn, neighbor_qn "
    "WITH qn, collect({qualified_name: neighbor_qn, "
    "                  provenance: provenance})[..$per_seed_limit] AS rows "
    "UNWIND rows AS row "
    "RETURN qn AS seed_qn, "
    "       row.qualified_name AS qualified_name, "
    "       row.provenance AS provenance"
)

_CALLEES_CYPHER = (
    "UNWIND $qns AS qn "
    "MATCH (target {qualified_name: qn}) "
    "MATCH (target)-[r:CALLS]->(callee) "
    "WITH qn, callee, collect(DISTINCT r.provenance) AS provs "
    "WITH qn, callee.qualified_name AS neighbor_qn, "
    "     CASE WHEN size(provs) > 1 THEN 'both' ELSE provs[0] END "
    "         AS provenance "
    "ORDER BY qn, neighbor_qn "
    "WITH qn, collect({qualified_name: neighbor_qn, "
    "                  provenance: provenance})[..$per_seed_limit] AS rows "
    "UNWIND rows AS row "
    "RETURN qn AS seed_qn, "
    "       row.qualified_name AS qualified_name, "
    "       row.provenance AS provenance"
)

_TESTS_CYPHER = (
    "UNWIND $qns AS qn "
    "MATCH (target {qualified_name: qn}) "
    "MATCH (test:Test)-[:COVERS]->(target) "
    "WITH qn, test.qualified_name AS neighbor_qn "
    "ORDER BY qn, neighbor_qn "
    "WITH qn, collect(neighbor_qn)[..$per_seed_limit] AS rows "
    "UNWIND rows AS neighbor_qn "
    "RETURN qn AS seed_qn, neighbor_qn AS qualified_name"
)
