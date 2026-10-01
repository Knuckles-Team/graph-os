# A2A task and approval projection

## Status legend

`READY FOR IMPLEMENTATION` describes this build contract, not working code. `BUILDING` means source is in progress. `SOURCE LANDED` means it is on the default branch. `ACCEPTED` requires exact default-branch contract, security, and served-path evidence from [test-spec.md](test-spec.md). Do not infer acceptance from a focused test or an A2A card alone.

## User stories and scope

An authenticated A2A caller can discover permitted agent skills, submit a task, get/list/cancel it, and resume a stream without creating a second task store. When an agent needs a human to approve a tool effect, the original human can review an `input-required` task and respond with a signed, single-use confirmation. A caller can invoke a GraphOS operation through A2A with the same scope, policy, effect and error semantics as MCP and HTTP.

GraphOS owns the transport, authenticated projection, operation adapter and served composition. The durable WorkItem/task state, human grant and atomic fences belong to the public engine and agent orchestration contracts. GraphOS must not persist a second task history, mint a human identity from a service token, or accept an unverified approval claim.

## Requirements

- **A2A-01 Native task methods.** Serve authenticated `/.well-known/agent-card.json` and JSON-RPC `/a2a` methods `message/send`, `message/stream`, `tasks/get`, `tasks/list`, `tasks/cancel`, and `tasks/resubscribe`. `message/send` requires an idempotency key; task IDs are opaque and tenant-bound. Get/list/cancel enforce the caller's durable authority. Stream/resubscribe use a bounded cursor and return ordered durable events, including terminal state, without lossy in-process task state.
- **A2A-02 Assembly and routing.** Incoming task dispatch uses the existing agent orchestration public port and an engine-backed WorkItem. Agent/capability selection uses caller-filtered assembly with budgeted tool subsets. The A2A task ID maps to the same durable parent/child WorkItem identities used by the dispatcher; routing ambiguity or missing public contract fails closed. The Agent Card advertises only currently usable, caller-authorized skills and methods.
- **A2A-03 Operation projection.** `graphos.op/invoke {op, params, plan_ref?, idempotency_key?}` passes the verified A2A caller to the hosted operation registry's `invoke` path with `Surface.A2A`. The exact operation scope, Eunomia, subject, effect, reservation, audit and error envelope are reused. Bespoke A2A elevation or direct child-tool execution is retired after equivalent ops are available.
- **A2A-04 Approval request.** A pending effect that needs `Confirm.PLAN` moves its durable task to `input-required` and emits `{task_id, pending_call_id, plan_ref, preview, expiry}` with no bearer material or raw secret params. The pending call and parent/child WorkItem relation are server-attested and durable across pause, restart and replica change. Console-class effects return `STEP_UP_REQUIRED` and a console URL; A2A cannot confirm them.
- **A2A-05 Approval response.** `graphos.plan/confirm` is a signed message from an authenticated human session. It must prove the original eligible human's identity and tenant, the pending call, parent/child task relation, operation and parameter digest, policy and registry revisions, unexpired plan, and a revocable human grant. An agent or service token alone cannot stand in for the human. One atomic compare-and-set consumes the grant/plan immediately before the effect; replay, changed params, different human, expired/revoked grant, canceled task, and stale policy fail without effect. Pending approval remains disabled until this full chain is proven end to end.
- **A2A-06 Failure and receipts.** Unknown methods return JSON-RPC method-not-found; malformed input returns a privacy-safe validation error; missing auth or scope, idempotency conflict, unavailable assembly, stale plan, and uncertain effect return stable distinct codes. Cancellation never claims to undo an already committed external effect. Every governed effect has durable audit reservation and linked outcome; a post-effect audit failure is indeterminate and reconciled.
- **A2A-07 Fresh checkout.** Contract and integration tests run with published packages, local fixture identities and ephemeral local services. No private network, sibling checkout or live production identity provider is required to review an external PR.

## Trace coverage

| IDs | GraphOS obligation | Delivery |
|---|---|---|
| GRAPHOS-A2A-R001, GRAPHOS-A2A-R008 | First-party native A2A facade over public engine and agent ports | NOT ACCEPTED |
| GRAPHOS-A2A-R002 | Authenticated inbound routing and caller-filtered assembly/tool subset | NOT ACCEPTED |
| GRAPHOS-A2A-R005 | Durable human tool-call approval exchange and fail-closed activation | NOT ACCEPTED |
| GRAPHOS-A2A-R006 | `graphos.op/invoke`, `graphos.plan/confirm`, shared operation registry and error projection | NOT ACCEPTED |
| GRAPHOS-A2A-R003, GRAPHOS-A2A-R004 | No fleet skill/prompt harvest duplicate; FastMCP 4 served bridge and one owner loop | NOT ACCEPTED |
| GRAPHOS-A2A-R007 | Pre-effect audit reservation and outcome linkage for approved effects | NOT ACCEPTED |

## Acceptance

1. A fresh public checkout boots A2A using published engine, agent and connector contracts and can run a unary task, durable stream/resubscribe and cancel/get/list against an ephemeral local stack.
2. A representative operation has identical authorization and error result through A2A, MCP and HTTP. A loaded child tool and A2A operation share one policy/audit path.
3. Human approval succeeds once across a pause/restart/replica boundary, and every negative case in [test-spec.md](test-spec.md) proves zero effect. The feature stays unavailable until that proof exists.
4. Quality gates pass at the exact default-branch revision with no duplicate task store, authority shim, private dependency, or scanner suppression.

Requirement IDs are defined in [requirements.md](requirements.md); delivery state per ID is in `status.json`.
