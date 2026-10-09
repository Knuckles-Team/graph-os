# Test and release contract

## Clean-checkout path

On a supported Python version, clone this repository, run `uv sync --extra test`, then `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, and `uv run mypy graph_os`. Run `uvx --from pre-commit==4.6.0 pre-commit run --all-files` for repository hooks. Contract tests use in-process fake public ports with deterministic data; they do not require a cluster, sibling checkout, credentials, or local inventory. Optional served tests may provision disposable dependencies in CI and must label that tier separately.

## Positive cases

| Test | Proof |
|---|---|
| Build a host with only installed public dependency packages | Core MCP and REST route tables load; optional capability availability is truthful. |
| Invoke identical read through MCP and REST | Same authorization, tenant filter, response semantic ID and source receipt. |
| Invoke a governed write through two entrypoints with same idempotency key | Exactly one downstream effect and one durable receipt; duplicate call returns the same outcome. |
| Open UI co-service, reload catalog and close it | One FastMCP loop/multiplexer, no blocked loop or lost generation notification. |
| Package wheel and import it in a fresh environment | Console scripts resolve and declared extras control optional imports. |
| GRAPHOS-HOST-R019: mount the dashboard routes; open `/ws/dashboard` and send a `subscribe` message | REST reads answer 200; the stream sends a snapshot, then an update scoped to the subscribed widgets. |
| GRAPHOS-HOST-R020: call `install_decide_consumers` with a fake client, then with a failing client, a failing assembler and a client without SPARQL | All four consumers install. Each fault skips only its own consumer and raises nothing. |
| GRAPHOS-HOST-R021: assemble a request through a fake engine client with the bound `commit_context`/`publish_context` providers | The fake client's `commit_decision` and `publish_graph` each record one call for a solved assembly; an abstained assembly records neither. |
| GRAPHOS-HOST-R022: install the decide consumers, then plan a task with a fake assembler and a fake capability source | The planner's `capability_search`/`guardrail_source`/`workflows` are bound and callable; the planned agent's skills/tools are non-empty and its reuse metadata reflects the fake capability source's hit. |

## Negative cases

| Test | Required refusal |
|---|---|
| Missing/expired principal, wrong tenant or scope, stale policy version | Deny before downstream effect; same error class on every surface. |
| Dependency public contract absent or wrong generation | Typed unavailable/mismatch result; no private-import fallback. |
| Duplicate server construction or co-service teardown race | No second authority; bounded shutdown and explicit failure. |
| Plant a forbidden `agent_utilities.knowledge_graph` or other private import | Package-layout check fails, including lazy import. |
| A route reimplements a stored task or graph operation | Architecture test detects a second persistence client/schema or unmatched action registration. |

## Quality and evidence

Run the repository's current CCCC, jscpd differential/census, dupehound changed/census, KISS and language-native gates from `.pre-commit-config.yaml` and the hosted scanner job when code changes. A new duplicate or complexity violation is fixed by reusing the owning module, not by a suppression or baseline reset. Evidence names the exact commit, command, package versions, CI run, artifact hash and representative served receipts; compare the final remote head to that commit. If an optional/live tier is unavailable, keep acceptance pending and report the missing tier precisely.
