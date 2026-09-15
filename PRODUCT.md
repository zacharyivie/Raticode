# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Developers are the primary users. The product must also be approachable for non-developers with a clear automation vision. Users build and run agentic workflows locally, either manually, on schedules, or when configured conditions occur.

## Product Purpose

Raticode lets people define, build, validate, and run local agentic workflows. It makes local automation practical for both interactive desktop use and remote, UI-free execution.

## Positioning

Raticode combines a local visual workflow studio with a CLI for remote machines and an in-product agent that can help construct workflows. It is designed to avoid relying on large agent-tool-call chains and oversized contexts, reducing operating cost while retaining local control.

## Operating Context

Users create graph-based workflows composed of shell commands, scripts, structured HTTP requests, and LLM agent calls. They run workflows on their own machine, manually, on a schedule, or in response to configured conditions. The desktop studio supports visual workflow work; the CLI supports remote boxes without a UI.

## Capabilities and Constraints

- The product runs locally and has no SaaS backend.
- It must not require login or account creation.
- Secrets stored by the app must be encrypted at rest.
- It must work across Windows, macOS, and Linux.
- The product includes a local visual workflow studio and a CLI.
- A built-in agent can help users build workflows.

## Brand Commitments

Local control, no-login operation, cost-conscious agentic automation, and a path from developer-grade capability to non-developer usability.

## Evidence on Hand

- Product documentation: `README.md`
- React/Electron workflow studio: `frontend/`
- Python CLI and workflow engine: `src/gofer/`
- No external SaaS backend or account system is part of the stated product model.

## Product Principles

1. Keep workflow execution and user control local.
2. Make advanced workflow automation understandable to people with clear intent, not only experienced developers.
3. Support the same workflow capability in a visual studio and on remote, UI-free machines.
4. Treat secrets and local data as security-sensitive product responsibilities.
5. Make agent assistance economically practical by avoiding unnecessary context and tool-call overhead.
