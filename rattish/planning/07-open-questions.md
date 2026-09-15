# Rattish open questions

These questions still need explicit decisions. Resolved questions move into the subject
documents rather than remaining in this list.

## `needs` activation

- How should diagnostics describe a conditional branch that leaves a partially satisfied
  requirement set unresolved?

## Predicates and routing

- What stable `error.kind` set does each built-in node contract declare?

## Terminal and failure presentation

- Should `finish: fail` run its cleanup operation when one of its requirements arrived as
  an allowed failure?
- How does the IDE distinguish tolerated-failure routes from successful routes?
- Should a workflow that passes with tolerated failures have a distinct display status,
  while retaining a successful machine outcome?

## Limits, time, scheduling, and recovery

- What cancellation grace period applies after timeout or FAIL?
- What happens to a local operation that was running when Gofer or the computer stops?
- Which recovery policies are author-configurable?
- Can Gofer reattach to any supported long-running provider or subprocess operations?

## Schemas and files

- How does portable source declare intentional access outside its project folder?

## Identity and registry

- Does a `.rattish` export omit the installed workflow ID in every case?
- Can a bundle request preservation of an ID, or does import always allocate against the
  destination registry?
- What registry action occurs if the source path moves inside the same workspace?
- May two installed workflow instances point at the same `.rattish` file?

## Metadata and editor

- What comment attachment rules survive the editor prototype?
- Does accepting an agent diff immediately write to disk, or may the editor retain an
  unsaved buffer?
- Can users opt into automatic acceptance of workflow-assistant changes?

## Bundles and migration

- What is the exact `.raticode` manifest shape and versioning policy?
- Which exclusion patterns are hard-coded and cannot be negated?
- Does export include files that prompts or scripts reference outside the project root?
- What is the product timeline for disabling legacy TOML execution?
- How are schedules and watchers transferred during conversion?
- Which persisted run states must survive an IR version upgrade?

## Plugins

- What syntax identifies a qualified plugin node type and its contract version?
- Must plugin contracts be installed for graph rendering, or can embedded contract metadata
  render an unavailable plugin node?
- How are plugin defaults frozen for reproducible recompilation?
