"""Read compatibility for files produced before the Raticode rename.

Keep the former spelling here so new files and public names use the current brand.
"""

LEGACY_WORKSPACE_DIRECTORY = ".taskurotta"
LEGACY_WORKFLOW_IGNORE = ".taskurottaignore"
LEGACY_BUNDLE_MANIFEST = "taskurotta.bundle.json"
LEGACY_BUNDLE_FORMAT = "taskurotta-workflow"

# Most recent spelling first when both generations exist.
LEGACY_WORKSPACE_DIRECTORIES = (".murina-code", LEGACY_WORKSPACE_DIRECTORY)
LEGACY_WORKFLOW_IGNORES = (".murina-codeignore", LEGACY_WORKFLOW_IGNORE)
LEGACY_BUNDLE_FORMATS = {
    "murina-code.bundle.json": "murina-code-workflow",
    LEGACY_BUNDLE_MANIFEST: LEGACY_BUNDLE_FORMAT,
}
