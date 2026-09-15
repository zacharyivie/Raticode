# Rattish conformance fixtures

This directory contains language-version fixtures rather than implementation-specific
tests. A conforming compiler must reproduce the expected AST, IR, diagnostics, and spans
after substituting the compile context recorded with each valid case.

Each valid case may contain:

- `workflow.rattish`, the authored source;
- `compile-context.json`, deterministic external compiler inputs;
- `expected-ast.json`, the strict-parser AST;
- `expected-ir.json`, canonical IR after semantic analysis and lowering;
- `expected-diagnostics.json`, warnings that do not block IR.

Each invalid case contains source and `expected-diagnostics.json`. Invalid cases must not
produce IR. Diagnostic message text is informative. Code, severity, phase, and span are
normative.

The Bash fixture stores readable full-document AST and IR snapshots. Full successful Loop
and child-workflow IR cases will join the pack when those contracts become executable.

The `contracts/` directory covers every machine-readable node contract. Its manifest
freezes complete canonical AST and IR documents with SHA-256 digests. Unsupported contract
fixtures freeze their capability diagnostic instead. Executable contracts also have an
invalid source fixture with an expected diagnostic code.
