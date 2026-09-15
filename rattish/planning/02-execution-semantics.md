# Rattish execution semantics

## Execution model

Rattish defines a routed graph with explicit readiness requirements. A node's `to` field
declares where completion may go. A target node's optional `needs` field declares the
predecessors that must have completed before it may run.

Declaration order has no effect on execution.

## Node executions and completion tokens

**Proposed formal model.** Each node execution is a distinct run. It completes with one
of these outcomes:

```text
success
failure
cancelled
```

A completion token records at least:

- Workflow run ID
- Activation lineage ID
- Activation-group ID
- Node ID
- Node run number
- Outcome
- Structured output when available
- Error details when failed

Activation lineage prevents a join from combining predecessor completions that belong to
different loop iterations or different parent activations.

An activation-group ID identifies route signals produced by the same logical fan-out. It
lets a downstream node coalesce several required branch completions into one execution.

## Start behavior

**Accepted.** Any node without `needs` receives one initial activation
when the workflow begins. Such a node may also receive later activations through incoming
routes.

`start: true` is optional syntactic sugar and an IDE styling hint. It does not create a
different execution rule. The compiler should reject `start: true` on a node that declares
readiness requirements.

**Accepted.** A workflow containing no nodes may parse, compile, and render in the graph
editor. Deployment preflight reports `RATTISH_WORKFLOW_EMPTY`, and the executor refuses to
run it with the same code.

## Routing with `to`

**Accepted.** `to` may target one node or split into several concurrent branches:

```rattish
to: review
```

```rattish
to:
  - implement-api
  - implement-ui
```

Every matching route activates. A route list is not an `if` and `else-if` chain.

Conditional routes may inspect structured output:

```rattish
to:
  - implement when not node.review.output.approved
  - complete when node.review.output.approved
```

**Proposed routing rules:**

- `when succeeded` matches a successful operation.
- `when failed` matches an allowed failed operation.
- A structured-output predicate is eligible only after success.
- An unconditional route matches any nonfatal completion.
- `otherwise` matches when no other conditional route matched.
- At most one `otherwise` route is allowed.
- Duplicate routes from one node to the same target are errors.
- When no route matches, the current branch terminates successfully unless a fatal outcome
  has already occurred.

## Readiness with `needs`

**Accepted.** Rattish has one readiness form. Every predecessor listed under `needs` must
complete before the target may run. Completion may be successful or an allowed failure.
An allowed failure satisfies the requirement only when the producer declares
`allow-fail: true`; a disallowed failure halts the workflow before routing. Branching
behavior comes from `to`, not from alternate readiness modes.

```rattish
Node review:
  needs:
    - implement-api
    - implement-ui
```

**Accepted.** Requirements form a one-time readiness latch within a workflow activation
lineage. Once every required predecessor has completed, the node remains unlocked for the
rest of that lineage. In the loop below, `c` can route back to `b` even though `b` names
only `a` as its requirement:

```text
a -> b <-> c
```

```rattish
Node b:
  needs:
    - a
  to: c

Node c:
  needs:
    - b
  to: b
```

The completion state and most recent successful output from `a` remain available when `b`
runs again. In `a -> b -> c -> a`, each later execution of `b` observes the most recent
successful output produced by `a` before `b` starts.

The runtime snapshots every available input when a node execution starts. A producer that
finishes after that point does not change the already-running consumer's inputs. Its route
signal may cause a later execution instead.

**Accepted.** An incoming `to` signal cannot activate a locked node. The executor retains
the signal as pending until every `needs` entry has completed at least once. Completing a
requirement updates the latch before the executor reevaluates pending signals.

For example:

```text
a -> b
c -> b
```

```rattish
Node b:
  needs:
    - c
```

If `a` routes to `b` before `c` completes, `b` remains locked. When `c` completes and
routes to `b`, the latch opens. The executor coalesces the pending signals from `a` and
`c`, then runs `b` once with both inputs.

The result depends on arrival time after the latch opens. If `c` completes and routes to
`b` before `a`, `c` satisfies the requirement and `b` is ready immediately. The executor
runs `b` with the input from `c`. When `a` later routes to `b`, that signal causes a second
execution with every input available at that later start time.

The executor processes a producer completion as one atomic scheduling event:

1. Record the producer's outcome and output.
2. Satisfy readiness requirements that name the producer.
3. Emit and record every matching `to` signal and its input.
4. Find newly runnable targets and snapshot their available inputs.

This ordering guarantees that the signal which opens a readiness latch is included in the
consumer's input snapshot.

**Accepted.** Route activations carry an activation-group ID. Signals that reach the same
target from the same activation group coalesce into one node execution. Thus, when
`implement-api` and `implement-ui` belong to one fan-out and `review` needs both, review
waits for both and runs once.

While a target is locked, the executor also coalesces all pending signals, including
signals from different activation groups, into the first execution that becomes runnable.
That execution receives every input recorded before its start. After the latch is open,
later signals retain their activation-group identity and may cause later executions.

## Per-node concurrency

**Accepted.** Every node has `max-concurrency: 1` by default. One execution of a node may
run at a time within one workflow run.

While a node is running:

- More signals from its active activation group coalesce and do not create another run.
- Signals from different activation groups queue in arrival order.
- Each queued execution snapshots all inputs available when that execution starts.
- The executor starts queued groups one at a time after the current execution finishes.

An author may explicitly raise `max-concurrency` when independent executions of the same
node may overlap. Coalescing still applies within each activation group.

## Routing and readiness consistency

**Accepted.** `to` and `needs` are intentionally asymmetric. A start node may route to
another node without having requirements. A loop-back route may target a node without
appearing in that node's requirements.

**Proposed compiler checks:**

- A `needs` predecessor does not need a direct route to the target. The compiler must be
  able to place it in the same possible activation lineage.
- An incoming route does not have to appear in the target's `needs`.
- Duplicate requirements are errors.
- A requirement that cannot complete on any possible path is an error.
- A node that can receive activation before all requirements complete waits rather than
  running early.

## Branches and joins

A route to several targets creates several live branch obligations. A successful sink
resolves one branch. A node waiting on several requirements joins those branches according
to the final `needs` lifetime and activation rules.

The workflow reaches quiescence when it has no running nodes, queued activations, or
partial joins that can still receive a completion.

## Unresolved joins

**Accepted.** A join does not fail because it has waited for an arbitrary duration. It
fails when the executor reaches quiescence and can prove that no running or queued work
can produce a missing completion.

An unresolved-join diagnostic should report:

- Waiting node
- Join mode
- Activation lineage
- Received predecessors
- Missing predecessors
- Last known state of missing producers
- Route conditions that prevented activation

Suggested diagnostic code:

```text
RATTISH_JOIN_UNRESOLVED
```

If no token ever reaches a join, the declaration alone does not create pending work. Once
a partial token set exists, the executor must resolve the join or report it as unresolved.

## Node failure

**Accepted.** Every node has:

```rattish
allow-fail: false
```

The field may be omitted because false is the default.

When a node with `allow-fail: false` fails:

- The workflow fails immediately.
- No outgoing route activates.
- Pending work is discarded.
- Active work receives cancellation.

When a node with `allow-fail: true` fails:

- The failure is recorded as tolerated.
- The failure produces a completion token.
- Eligible outgoing routes activate.
- Downstream readiness may consume the failed completion.
- The workflow may eventually pass.

**Accepted.** An unconditional route matches both success and allowed failure. A
`when failed` route on a node that does not allow failure is a compile error because that
route is unreachable.

**Accepted.** `otherwise` matches when no conditional route from that completion matched.
Unconditional routes fire independently and do not suppress `otherwise`. An unconditional
route and an `otherwise` route from the same node to the same target are a compile error.

Detailed failure routing reads the common error object, such as:

```rattish
to:
  - retry when node.request.error.kind == timeout
  - report when failed
```

## Output availability after failure

A failed execution may lack its declared structured output. Status and error information
remain available through fields such as:

```text
node.test.status
node.test.error.code
node.test.error.message
```

**Proposed.** If a binding may observe an allowed failed producer, the compiler treats the
producer's normal output as optional. The binding must provide a default or route failure
to a consumer that does not require that output.

Each node retains its most recent successful structured output within the current workflow
run and activation lineage. A later allowed failure updates status and error information
but does not erase the previous successful output. If the node has never succeeded, its
normal output is absent and a consumer needs an explicit binding default.

## PASS, FAIL, and ordinary termination

**Accepted.** `start`, `finish: pass`, and `finish: fail` are optional declarations. A
branch that completes its routed work without a fatal failure passes even when it has no
explicit PASS node.

`finish: pass` is syntactic sugar for an explicit successful sink. It helps IDE syntax
highlighting and graph styling. It cannot have outgoing routes.

`finish: fail` is an explicit cleanup terminal. After its cleanup operation completes, the
workflow fails and halts. It cannot have outgoing routes. `finish: fail` takes precedence
over `allow-fail`, so failure of the cleanup operation cannot turn the intended workflow
failure into a pass.

**Accepted.** Declaring both `finish: fail` and `allow-fail: true` is a compile error. The
IDE flags the conflicting fields. Omitting `allow-fail` uses its false default.

## Workflow result

**Accepted in principle.** A workflow passes when it reaches quiescence and none of these
events occurred:

- A node with `allow-fail: false` failed.
- An explicit FAIL terminal completed.
- A workflow or node run limit was exhausted.
- An unresolved partial join remained.
- Another fatal runtime error occurred.

Allowed node failures are recorded but do not prevent a passing result.

## Cycles

**Accepted.** Cycles are supported. A common review loop is:

```rattish
Node implement:
  type: agent
  to: review

Node review:
  type: agent
  needs:
    - implement
  to:
    - implement when not node.review.output.approved
    - complete when node.review.output.approved
```

`implement` starts because it has no readiness requirement. It may run again when review
routes back to it.

## Breaking loops

**Accepted.** `break` remains a built-in control node. It names its target loop explicitly:

```rattish
Node stop-research:
  type: break
  loop: research-loop
  message: Enough results were found
```

The compiler requires `loop` to name a loop whose activation lineage can reach the Break
node. A Break node cannot have `to`.

When Break completes, it succeeds and closes only the named loop activation lineage. The
executor discards iterations and loop-owned activations that have not started. Executions
already running finish their current branches. The loop reaches completion after those
branches settle, then its completion routes may run. Multiple Break executions for the
same loop lineage have the same effect as one.

Rattish 1 does not support cancelling already-running iterations as part of Break.

## Run limits

**Accepted.** Exceeding `max-runs` fails and halts the workflow because the workflow could
not complete its normal steps.

The design supports two scopes:

```rattish
Workflow:
  max-runs: 100
```

```rattish
Node review:
  max-runs: 10
```

The workflow field limits total node executions. The node field limits executions of that
node. When the next activation would exceed either limit, the executor does not start it
and fails the workflow.

**Accepted.** Workflow and node `max-runs` both default to `none`. Rattish does not stop a
workflow after an arbitrary number of executions. Authors may add limits when exhaustion
should be treated as workflow failure.

## Operation retries

**Accepted.** One routed activation is one node run. It consumes one node and workflow
`max-runs` unit before its first operation attempt. `retry-count` adds operation attempts
inside that run and does not consume more run-limit units.

Every attempt uses the input snapshot captured when the node run started. A producer that
finishes between attempts cannot alter the retry's inputs. Each attempt receives a fresh
node timeout. `retry-delay` time counts toward the workflow timeout but not the node
timeout. The node publishes one completion token after success or after the last failed
attempt. `allow-fail` applies only to that final result.

## Node timeout

**Accepted.** Node timeout defaults to `none`. Long-running agentic tasks must not be cut
off by an arbitrary language default.

```rattish
timeout: none
timeout: 30s
timeout: 15m
timeout: 18h
timeout: 2d
```

**Proposed duration rules:**

- A literal contains one positive integer and one unit.
- Supported units are milliseconds, seconds, minutes, hours, and days.
- Compound values such as `1h 30m` are not part of Rattish 1.
- The timeout starts when the operation starts.
- Readiness waiting and concurrency queue time do not count.
- The IR stores integer milliseconds or `null`.
- Preflight does not warn when timeout is absent.

A timeout is an operation failure. `allow-fail` determines whether it fails the workflow or
produces a tolerated failure completion.

## Workflow timeout

**Accepted.** A workflow may declare an active-processing timeout, also defaulting to
`none`:

```rattish
Workflow:
  timeout: none
```

This is distinct from `max-runs`.

**Accepted.** Configured timeouts count active processing time. Time while the background
runtime is stopped does not consume a node or workflow timeout. The runtime persists
accumulated processing duration and resumes timing after recovery. Recovery behavior for
an operation whose process disappeared during downtime remains open.

For a workflow timeout, active processing time includes elapsed time in running
operations, readiness waits, unresolved joins, and concurrency queues while the
background runtime remains alive. A node timeout continues to count only that node's
active operation time.
