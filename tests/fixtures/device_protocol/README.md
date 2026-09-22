# Public interoperability test identities

These private keys are intentionally public, disposable test material. Never
load them into a real device registry or use them for pairing. `pins.json`
contains SHA-256 hashes of DER SubjectPublicKeyInfo, not certificate hashes.

The self-signed P-256 certificates are valid from 2025 through 2035 to make
cross-language tests reproducible. Production identities require OS-protected
storage and a separate enrollment flow. This directory grants no trust.
