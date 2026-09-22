# Desktop protocol recovery

Recovered retained revision `d39227d4ae607f57272097399402be9c0417ee58`
into replacement assignment `2807e2239eaf4c4fbb1ef9b614c9e659`.
All 67 changed files match the retained committed bytes, including source,
dependency lock, shared fixtures and original evidence. `recovery.json` records
their SHA-256 values and fresh local check results. Only this recovery evidence
is added. Original checkouts and retained attempts were not modified.

A fresh assignment-local environment was created with
`uv sync --locked --extra dev --extra devices`. Targeted pytest passes all 109
tests, Ruff passes, and mypy passes on 233 source files. Recovery logs sit beside
this document. Managed verification and the new candidate revision are recorded
on the swarm board after the managed commit. The older README accurately records
the retained attempt's limitations; its missing managed verification is historical.

The frozen v2 manifest remains
`f3faf4bb173b6fe4238530cf271c9fbe647025ce7587195cb1e7401ac9ba8e60`.
Retained mobile protocol test artifacts remain evidence of the runs identified
in `mobile-interop.json`, not a fresh full mobile validation. Mobile's final
recovery revision and exact review remain owned by Mobile dev and its reviewer.
The reported landscape Back/IME failure remains tracked for the mobile repair.

This recovery adds no registry, service, transport integration, Rem dispatch,
file service or UI behavior. Subsequent milestones remain dependent on accepted
protocol work. Agent review does not satisfy independent cryptographic review;
physical-device and public-network validation remain open.
