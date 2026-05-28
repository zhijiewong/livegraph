# livegraph Phase 7 — Embeddings + `semantic_search` Design Specification

- **Date:** 2026-05-24
- **Status:** Approved (design); pending implementation plan
- **Scope of this spec:** A new `livegraph embed` CLI command that computes local vector embeddings (default `all-MiniLM-L6-v2`, 384 dimensions) for every `Function` and `Method` node via `sentence-transformers`, stores them on the nodes, and creates a Neo4j vector index. A new `semantic_search` MCP tool (the 14th) answers "find me code that does X" queries by cosine similarity. The ML dependency stack lives behind an opt-in `pip install livegraph[semantic]` extra.
- **Out of scope (future):** pluggable embedding providers (OpenAI / Voyage / Ollama), auto-re-embed during `livegraph build` / `livegraph update`, embedding `Class` or `File` nodes, per-symbol chunked embeddings for very long functions, multi-language support, hybrid vector+structured queries.
- **Builds on:** Phases 1+2 (graph), 3 (MCP server, 13 tools), 4 (`change_impact`), 5 (incremental updates, `runtime_stale` flag), 6 (`describe_schema`, `run_cypher`).

---

## 1. Overview

`livegraph embed` walks a configured project's `Function` and `Method` nodes, computes one vector embedding per node using a locally-loaded `sentence-transformers` model, and writes the vectors back as Neo4j properties plus a vector index. A new `semantic_search` MCP tool embeds the agent's query string with the same model and uses Neo4j's vector index to return cosine-similarity-ranked code symbols.

Staleness is tracked via `embedding_source_hash` — the same pattern Phase 5 uses for `content_hash` on files. Re-running `livegraph embed` after `livegraph update` re-embeds only the symbols whose source actually changed.

The entire ML stack is opt-in via a `[semantic]` pip extra. Users who don't want semantic search keep the lean install; the `semantic_search` tool returns a structured error with the install hint, and no other tool is affected.

## 2. Rationale

Phase 6 closed the "agent wants to ask arbitrary structured questions" gap with `describe_schema` + `run_cypher`. Phase 7 closes a different gap: questions where the *concept* is clear but the right *names* aren't.

An agent that wants to know "where do we handle JWT verification?" can't easily compose Cypher for it — there's no `:Concept` label. But a vector model trained on code recognizes the semantic neighborhood: `verify_token`, `decode_jwt`, `check_signature`, `authenticate_bearer`. Embeddings collapse that name-distance into vector-distance.

The Phase 1+2 research called out `code-graph-rag` for shipping UniXcoder embeddings as a core feature. Phase 7 follows the same playbook with three deliberate choices that distinguish it:

1. **Optional install** — embedding deps don't tax users who don't want them.
2. **Source-hash staleness tracking** — `livegraph embed` is fast on no-op re-runs, mirroring the rest of livegraph's idempotent design.
3. **Single shared `:Symbol` label** — keeps the query surface one-index, one-call, no UNION re-ranking.

## 3. Scope

**In scope:**

- A new `livegraph/semantic/` package with `provider.py` (the `EmbeddingProvider` protocol + one `LocalSTProvider` implementation) and `embed.py` (the orchestrator).
- A new `livegraph embed [--project NAME] [--rebuild]` CLI subcommand.
- A new `semantic_search(query, limit, kind)` MCP tool (tool 14).
- Three new node properties on `Function` and `Method`: `embedding`, `embedding_source_hash`, `embedding_model`.
- A new `:Symbol` secondary label on every embedded Function and Method (the anchor for the shared vector index).
- A Neo4j vector index `livegraph_symbol_embeddings` on `(:Symbol)`.
- Two new `Settings` fields: `livegraph_embed_model` (default `all-MiniLM-L6-v2`), `livegraph_embed_batch_size` (default 32).
- A new `pyproject.toml` optional extra `semantic = ["sentence-transformers>=3.0"]`.
- Unit, integration (gated on the `[semantic]` extra), and CLI tests.
- A README section.

**Out of scope (future, separate specs):**

- Pluggable providers (OpenAI / Voyage / Ollama / sentence-transformers via a different model family).
- Auto-re-embed during `livegraph build` or `livegraph update`.
- Embedding `Class` or `File` nodes (their methods are individually embeddable).
- Chunked per-symbol embeddings for functions that exceed the model's token limit.
- Hybrid vector+structured queries (the agent composes them itself using `semantic_search` + `find_callers` etc.).
- Multi-language support.
- Cross-project search.
- A `livegraph search "natural language"` human-facing CLI subcommand.

## 4. Tech Stack

New optional runtime deps (behind `[semantic]` extra):

- **`sentence-transformers>=3.0`** — the high-level wrapper around HuggingFace transformers. Pulls `torch` transitively.

No new credentials, no API keys. The default model `all-MiniLM-L6-v2` is ~80 MB and downloads on first use into the user's HuggingFace cache.

The `sentence-transformers` import lives inside `LocalSTProvider.__init__`, not at module top. Without the extra, the rest of livegraph imports normally; only the `embed` CLI and `semantic_search` tool surface the install hint.

## 5. Architecture

### Module layout

```
livegraph/
  semantic/
    __init__.py
    provider.py     NEW — EmbeddingProvider protocol + LocalSTProvider
    embed.py        NEW — walk graph, compute embeddings, write back
  mcp/
    tools.py        + semantic_search()
    server.py       + @mcp.tool() wrapper (tool 14)
  cli.py            + `embed` subcommand
  config.py         + livegraph_embed_model
                    + livegraph_embed_batch_size
pyproject.toml      + [project.optional-dependencies] semantic
```

### Data flow for `livegraph embed`

```
1. Load configured model lazily (only when needed)
2. Query Neo4j for Function/Method nodes that need (re-)embedding:
     - no embedding at all, OR
     - embedding_source_hash != sha256(current source), OR
     - embedding_model != configured model
3. Detect dimension mismatch up front; refuse with --rebuild hint if
   the existing index has different dimensions than the new model
4. Batch through provider.encode() in chunks of livegraph_embed_batch_size
5. Per node, write back: :Symbol label, embedding, embedding_source_hash,
   embedding_model
6. Create the vector index if absent (or drop+recreate on --rebuild)
7. CALL db.awaitIndexes() to ensure the index is queryable
8. Print summary: N embedded, M unchanged, K skipped
```

### Data flow for `semantic_search`

```
agent -> semantic_search(query, limit, kind)
   |
   v
1. Embed query via the same provider. The provider is constructed
   lazily on first semantic_search call (first call is slow, ~3s of
   model load; subsequent calls reuse the loaded model). Without the
   [semantic] extra the construction raises EmbeddingExtraMissing,
   which becomes a structured MCP error.
   |
   v
2. CALL db.index.vector.queryNodes(
       'livegraph_symbol_embeddings', $k_padded, $query_vector
   ) YIELD node, score
   |
   v
3. Post-filter results: project scope (traverse through :Project),
   kind filter (function / method / any), source snippet
   |
   v
4. Trim to `limit` results; return { results, model,
                                       embedded_count, warning }
```

### Cross-cutting decisions (locked, won't re-litigate)

- **`livegraph update` does NOT automatically re-embed.** Source-hash mismatches are detected on the next `livegraph embed` invocation.
- **Tests get embedded.** Test function bodies describe the behavior under test — valuable semantic signal. `kind="function"` excludes `:Test` nodes; `kind="any"` (default) includes them.
- **Classes are NOT embedded.** Class-level matching dilutes signal; methods are individually embeddable.
- **One model per project at a time.** Switching models means `--rebuild`. Mixed-model vectors in one index produce bad results silently and are rejected up front.

## 6. Embedding Generation

### The `EmbeddingProvider` protocol

```python
class EmbeddingProvider(Protocol):
    name: str            # e.g., "all-MiniLM-L6-v2"
    dimensions: int      # e.g., 384

    def encode(self, texts: list[str]) -> list[list[float]]:
        """Embed each text. Order-preserving. Returns len(texts) vectors."""
```

One implementation in v1: `LocalSTProvider`. Its `__init__` does the lazy `from sentence_transformers import SentenceTransformer` import; absence raises a typed `EmbeddingExtraMissing` exception.

### What we embed

Every node satisfying `(s:Function OR s:Method)` in the configured project with a non-empty `source` property. We pass the raw `source` text to the model. No docstring extraction, no signature-only mode, no preprocessing. The model's tokenizer handles truncation (MiniLM's 512-token cap; documented limitation).

A symbol with empty or missing `source` (e.g., a `runtime_only=true` symbol from a parse-failed file) is skipped and counted in the embed summary.

### Staleness detection (mirrors Phase 5 exactly)

Three properties per embedded node:

| Property | Type | Set by |
|---|---|---|
| `embedding` | `list[float]` (length = `provider.dimensions`) | `livegraph embed` |
| `embedding_source_hash` | hex SHA-256 of `source` | `livegraph embed` |
| `embedding_model` | provider name | `livegraph embed` |

`livegraph embed` queries:

```cypher
MATCH (:Project {name: $project})-[:CONTAINS]->(:File)
      -[:DEFINES|HAS_METHOD*1..2]->(s)
WHERE (s:Function OR s:Method)
  AND s.source IS NOT NULL AND s.source <> ""
RETURN s.qualified_name AS qn, s.source AS source,
       s.embedding_source_hash AS prior_hash,
       s.embedding_model       AS prior_model
```

The Python orchestrator computes SHA-256 of each `source` and filters to symbols where `prior_hash` differs from the current hash OR `prior_model` differs from the configured model OR both are null. Surviving symbols are batched through `provider.encode()`.

### The `--rebuild` escape hatch

```
livegraph embed --rebuild
```

Drops the vector index, clears every `:Symbol` label and `embedding*` property in the project, and re-embeds from scratch. Required when switching to a model with different dimensions. Without `--rebuild`, a dimension mismatch is detected before any writes and the command refuses with a clear message.

### Batching and progress

`livegraph_embed_batch_size` (default 32). One `provider.encode()` call per batch. Per-batch progress is printed for long-running embeds.

### Failure handling

| Failure | Behavior |
|---|---|
| `[semantic]` extra not installed when running `livegraph embed` | Exit code 1; print `Install 'livegraph[semantic]' to use embeddings (adds sentence-transformers + torch, ~2 GB)` |
| Provider raises during `.encode()` | Stop, print the error, exit code 2. Prior batches' writes stay (idempotent recovery). |
| Configured project has no embeddable symbols | Complete with `0 embedded, 0 unchanged`; no index created |
| Dimension change without `--rebuild` | Refuse before writes with `dimensions changed (384 -> 768); pass --rebuild` |
| Neo4j unreachable | Standard connectivity error |

## 7. Storage: `:Symbol` Label + Vector Index

### The `:Symbol` secondary label

Every Function or Method that gets embedded also gets a `:Symbol` label attached during the embed write. This is the same trick Phase 2 used for `:Test` — a no-op secondary label that lets us anchor one vector index across both `:Function` and `:Method` nodes.

The label is idempotent (MERGE pattern), invisible to all existing queries, and lets us write one query in `semantic_search` instead of UNION-merging two label-specific queries.

Per-node write (per batch):

```cypher
UNWIND $rows AS row
MATCH (s {qualified_name: row.qn})
WHERE s:Function OR s:Method
SET s:Symbol,
    s.embedding = row.embedding,
    s.embedding_source_hash = row.hash,
    s.embedding_model = row.model
```

### The vector index

```cypher
CREATE VECTOR INDEX livegraph_symbol_embeddings IF NOT EXISTS
FOR (n:Symbol)
ON (n.embedding)
OPTIONS {
  indexConfig: {
    `vector.dimensions`: $dimensions,
    `vector.similarity_function`: 'cosine'
  }
}
```

Cosine similarity matches sentence-transformers' default normalization. Dimensions come from `provider.dimensions`. After creation, `CALL db.awaitIndexes()` blocks until the index is queryable.

### Querying the index from `semantic_search`

```cypher
CALL db.index.vector.queryNodes(
    'livegraph_symbol_embeddings', $k_padded, $query_vector
) YIELD node, score
WITH node, score
MATCH (:Project {name: $project})-[:CONTAINS]->(:File)
      -[:DEFINES|HAS_METHOD*1..2]->(node)
WHERE ($kind = 'any' AND (node:Function OR node:Method))
   OR ($kind = 'function' AND node:Function AND NOT node:Test)
   OR ($kind = 'method' AND node:Method)
RETURN node.qualified_name AS qualified_name,
       node.name AS name,
       head([l IN labels(node)
             WHERE l IN ['Function','Method'] | toLower(l)]) AS kind,
       node.file AS file,
       node.start_line AS start_line,
       node.end_line AS end_line,
       coalesce(node.source, '') AS source,
       score
ORDER BY score DESC
LIMIT $limit
```

`$k_padded = limit + 50` so the post-filter has headroom after kind/project rejections. The vector index has no built-in project or label awareness; the post-filter is the actual scope enforcement.

### Dimension-mismatch protection

Before writing any embedding batch:

```python
existing_dims = _read_existing_index_dimensions(backend)
if existing_dims is not None and existing_dims != provider.dimensions:
    raise EmbeddingDimensionMismatch(
        f"Existing index uses {existing_dims} dimensions; new model "
        f"'{provider.name}' produces {provider.dimensions}. "
        f"Pass --rebuild to drop the existing index and start over."
    )
```

`--rebuild` drops the index, clears every `:Symbol` label and `embedding*` property in the project, and starts fresh.

### Summary of what gets stored

| Where | What |
|---|---|
| Per Function/Method node | `:Symbol` label, `embedding`, `embedding_source_hash`, `embedding_model` |
| Database-wide | `livegraph_symbol_embeddings` vector index (dimensions baked at creation) |

No separate vector DB, no separate process, no schema migration needed for users who never run `livegraph embed`.

## 8. CLI, Configuration & MCP Tool

### CLI

```
livegraph embed [--project NAME] [--rebuild]
```

Resolves project via `--project` then `LIVEGRAPH_PROJECT` env. `--rebuild` clears existing embeddings + index before starting. Standard error contract with the rest of livegraph (exit code 2 for missing project, code 1 for Neo4j unreachable / missing extra).

Sample output (success):

```
Project: sample
Loading model: all-MiniLM-L6-v2 (384 dims)... done.
Found 27 symbols needing embedding (3 new, 24 source-changed, 13 unchanged).
Embedding batch 1/1 (27 symbols)... done in 1.4s.
Awaiting index 'livegraph_symbol_embeddings'... ready.
Embed complete: 27 embedded, 13 unchanged, 0 skipped.
```

### Configuration

Two new `Settings` fields:

```python
livegraph_embed_model: str = "all-MiniLM-L6-v2"   # LIVEGRAPH_EMBED_MODEL
livegraph_embed_batch_size: int = 32              # LIVEGRAPH_EMBED_BATCH_SIZE
```

Any HuggingFace model id can be configured; the user is responsible for dimension management and re-running with `--rebuild` when switching to an incompatibly-dimensioned model.

### The `semantic_search` MCP tool (tool 14)

```python
semantic_search(query: str, limit: int = 10,
                kind: str = "any") -> dict
```

Returns:

```python
{
  "results": [
    {
      "qualified_name": "src/app/auth.py::verify_token",
      "name": "verify_token", "kind": "function",
      "file": "src/app/auth.py", "start_line": 12, "end_line": 28,
      "score": 0.87,
      "snippet": "first ~3 non-blank lines of source",
    }, ...
  ],
  "model": "all-MiniLM-L6-v2",
  "embedded_count": 142,
  "warning": None,  # or "no embeddings yet; run `livegraph embed` first"
}
```

A graph that's been ingested but never embedded returns `results: []` with the warning set so the agent knows to instruct the user to run `livegraph embed`.

When the `[semantic]` extra is missing, the tool returns a structured MCP error: `"semantic search not enabled — install with 'pip install livegraph[semantic]'"`.

### `pyproject.toml` extra

```toml
[project.optional-dependencies]
dev = [...]   # existing
semantic = ["sentence-transformers>=3.0"]
```

## 9. Error Handling (consolidated)

| Failure | Behavior |
|---|---|
| `[semantic]` extra not installed when running `livegraph embed` | Exit 1; clear install hint |
| `[semantic]` extra not installed when `semantic_search` MCP tool is called | Structured MCP error with install hint; server stays up |
| `--project` missing and `LIVEGRAPH_PROJECT` unset | Exit 2 |
| Neo4j unreachable | Exit 1 with the connection target |
| Empty project (no embeddable symbols) | Complete with `0 embedded`; no index created |
| Dimension change without `--rebuild` | Refuse before writes with clear hint |
| Model load failure | Exit 2; prior batches' writes stay |
| `semantic_search` called when vector index doesn't exist | Return `results: []` + warning |
| Provider raises during `.encode()` | Stop, print, exit 2 |

## 10. Testing Strategy

**Unit (no Neo4j, no model):**

- `tests/unit/test_embedding_provider.py` (~4 tests) — `LocalSTProvider` lazy-load behavior; without `[semantic]` raises `EmbeddingExtraMissing`; mocked `SentenceTransformer` returns correct shape.
- `tests/unit/test_embed_orchestrator.py` (~6 tests) — embed pipeline against a queued backend with a mock provider. All-new project, no-op re-run, single-file change, empty-source skip, dimension-mismatch refusal, `--rebuild` clear.
- `tests/unit/test_mcp_tools_semantic_search.py` (~5 tests) — `semantic_search` against `FakeBackend`. Result shape, `embedded_count=0` warning, `kind` filter passthrough, `limit` passthrough, missing-extra error.

**Integration (real Neo4j, `[semantic]` extra installed):**

`tests/integration/test_semantic_integration.py` — marked both `@pytest.mark.integration` and `@pytest.mark.semantic`. Reuses Phase 3's `ingested_sample` fixture.

1. **End-to-end embed**: every Function and Method gets `:Symbol`, `embedding`, `embedding_source_hash`, `embedding_model`; vector index exists with correct dimensions.
2. **`semantic_search` smoke**: `semantic_search("addition arithmetic")` returns `calculator.py::Calculator.add` in the top 3.
3. **Idempotent re-run**: second `livegraph embed` re-embeds zero symbols.
4. **Source-change detection**: modify one Function's `source` directly in the graph; re-run `embed`; exactly that symbol is re-embedded.
5. **`--rebuild` works**: vectors are replaced, index recreated.
6. **`embedded_count` reflects reality**: drop one node's `embedding`; `semantic_search.embedded_count` decreases by one.

The integration tests are skipped on machines without the `[semantic]` extra by gating on `pytest.mark.semantic`. CI configurations can opt in.

## 11. Repo Layout After Phase 7

```
livegraph/
  semantic/
    __init__.py
    provider.py                          NEW
    embed.py                             NEW
  mcp/
    tools.py                             + semantic_search()
    server.py                            + @mcp.tool() wrapper (14)
  cli.py                                 + `embed` subcommand
  config.py                              + 2 new settings
pyproject.toml                           + [optional-dependencies] semantic
tests/
  unit/
    test_embedding_provider.py           NEW (~4 tests)
    test_embed_orchestrator.py           NEW (~6 tests)
    test_mcp_tools_semantic_search.py    NEW (~5 tests)
  integration/
    test_semantic_integration.py         NEW (~6 tests, gated)
README.md                                + semantic search section
```

## 12. Risks

| Risk | Mitigation |
|---|---|
| MiniLM truncates very long functions at 512 tokens | Documented limitation; semantic recall on long bodies is degraded but works on signatures + early body |
| First-time embed is slow (model download + load) | Print the model name + dim count up front; subsequent runs reuse the HF cache |
| Vector index dimensions are immutable | `--rebuild` with destructive-clear; documented |
| sentence-transformers version produces different vectors over time | `embedding_model` records the name only; user runs `--rebuild` on regression |
| Test functions clutter semantic_search results | `kind="function"` excludes `:Test`; documented |
| `[semantic]` install fails on user machines (torch wheel issues) | Documented; livegraph core remains functional |
| Cross-project node-sharing (Phase 1 limitation) | Defensive scoping through `(:Project)-[:CONTAINS]->...` in the semantic_search query |
| HuggingFace download requires network | Document; once cached, subsequent runs are offline |
| Long source bodies make per-row payload size matter | Existing `batch_size` already controls batch payload; default 32 is well below MCP message limits |

## 13. Future Work (out of scope)

- A pluggable provider system shipping local + OpenAI + Voyage + Ollama backends with one configured at runtime.
- Auto-re-embed during `livegraph build` / `livegraph update` (optional flag).
- Chunked per-symbol embeddings: split very long functions into multiple vectors and aggregate at query time.
- Hybrid queries combining vector similarity with structural predicates in one server-side call (`semantic_search_with_callers(query, called_by)` etc.).
- Embedding Class nodes (with class-summary text derived from method bodies + docstrings).
- Cross-project semantic search.
- A human-facing `livegraph search "natural language"` CLI subcommand.
- Multi-language support (TypeScript) — Phase 1 prerequisite first.
