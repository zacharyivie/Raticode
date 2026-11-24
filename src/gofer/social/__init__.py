"""Social posts: Rem, tasks and workflows propose; only the user approves; Raticode publishes.

``gofer.social`` holds the post store, the state machine, the approval hash, the policy
checks, the account index and keychain entries, and the publisher. It imports no UI code
and is the only code that reads a social account's login. Each platform has one adapter
in ``gofer.social.providers``; adapters send what they are given and never decide whether
something should publish.

The rules that keep this safe:

- Only the actor ``"user"`` approves a post, taps Publish on a due post, sends an
  outcome-unknown post again or changes accounts. Agent tokens, Rem's tools, the CLI
  inside an agent turn and workflows can only propose and revise their own proposals.
- An approval covers one content hash: every variant's text, link, media hashes, account
  and options, plus the time. Any edit clears it and the post goes back to review.
- The publisher runs no model. It sends exactly the approved bytes.
- A send whose result is unknown is never sent again automatically.
"""
