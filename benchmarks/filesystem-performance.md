# Filesystem performance checks

Select Node with `nvm use` from the repository root. Run:

```sh
node benchmarks/filesystem_performance.cjs
node benchmarks/filesystem_performance.cjs /absolute/path/to/representative/project
node benchmarks/git_performance.cjs
```

The first command creates and removes its own 1,000-file fixture. The optional project
is read-only. The benchmark compares identical search results with pools of 1, 2 and
4, records five warm samples per mode, and reports a 5,000-message asynchronous log
burst. It does not execute workflows. Timings include Git candidate discovery or its
filesystem fallback. They do not measure cold startup, Defender contention, or UI
frame latency. The log delay sample is a rough process-level check, not a desktop
interaction benchmark.

Search remains serial by default. For a native Windows experiment, launch the app
with `TASKUROTTA_SEARCH_READ_CONCURRENCY=2` or `4`. Values are capped at four.
Use an idle local NTFS project and compare both clean and heavily modified trees.
Test x64 and ARM64 processes separately. Also run the fixture on macOS and Linux
before changing the default. Preserve the result equivalence check. Stop if the
project changes while benchmarking.

## Watcher behavior

The background watcher uses watchdog's platform Observer. File notifications mark
a watch dirty; the existing sorted snapshot diff still decides trigger events.
A clean watch performs a root identity check rather than a complete glob scan.
Reconciliation runs every 30 seconds, and explicit `poll_once()` still forces a
scan. Missing paths and unavailable native watches retain polling. Replacing a
watched root restarts its observer. Linux and macOS keep their native observers.
This remains snapshot-based: a file created and removed between snapshots is not
promised as a trigger. A missed native notification may delay detection until
reconciliation. Debounce, concurrency and queue limits retain their prior behavior.

## Git filesystem monitor experiment

No application code enables a Git monitor or changes Git configuration. Porcelain
v2 already reduces a warm source-control refresh from three subprocesses to one.
Editor baseline status omits branch headers so it does not gain an ahead/behind
history walk.

Consider the built-in monitor separately on native Windows or macOS with Git
2.37 or newer, preferably a current maintained release. Git documents platform
support at <https://git-scm.com/docs/git-fsmonitor--daemon>. Check `git --version`
and `git fsmonitor--daemon -h` first. Reject UNC paths, mapped network drives,
removable volumes and unknown filesystem types. On Windows, verify the drive is
local and NTFS through Windows storage information; a drive letter alone is not
proof. Keep Git's default remote-filesystem restrictions. Linux support depends on
the installed Git build and is outside this initial experiment.

Use a disposable clone on the verified local volume. Start its monitor explicitly
with `git -C <clone> fsmonitor--daemon start`. Compare repeated
`git -C <clone> -c core.fsmonitor=false status --porcelain=v2 --branch -z`
and the same command with `-c core.fsmonitor=true`, using a new process for each
sample. Both outputs must agree after external edits, renames, directory moves,
checkout and untracked-file creation/deletion. Include a large untracked tree and
measure idle monitor resource use. Stop it with
`git -C <clone> fsmonitor--daemon stop` in cleanup. Command-local `-c` overrides
avoid changing repository or global settings. Do not add `--global` configuration
or enable remote-volume exceptions. No monitor experiment was run on Windows from
this Linux workspace.
