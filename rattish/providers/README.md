# Rattish provider contracts

Provider contracts make Agent compilation portable. They describe stable provider
identity and authoring defaults; they do not assert that a provider executable,
credential, profile, or model is available on the compiling machine. Deployment
preflight owns those checks.

Each version 1 contract contains:

- `provider_id`: the canonical kebab-case identifier used in `.rattish` source and IR;
- `contract_version`: the version frozen into IR dependencies;
- `runtime_subscription`: the current Raticode runtime adapter;
- `default_model` and `default_effort`: values frozen into IR when omitted in source.

Contract contents are schema-validated and fingerprinted. Their fingerprints participate
in the compilation-cache key, so changing a default invalidates cached artifacts.

