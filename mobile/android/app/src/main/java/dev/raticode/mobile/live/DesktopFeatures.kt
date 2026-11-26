package dev.raticode.mobile.live

/**
 * What the connected desktop told this phone it takes, from its overview (and, for a few, its Rem home view).
 * An older desktop rejects an op it doesn't know and closes the session, so the phone sends each one only after
 * the matching field is on. Every field is false until this session's overview arrives: a reconnect or desktop
 * control turning off goes back to [NONE] in one assignment.
 */
data class DesktopFeatures(
    /** The desktop fetches message images for the phone. */
    val media: Boolean = false,
    /** The desktop sends the file behind a tapped file link. */
    val fileLinks: Boolean = false,
    /** The desktop offers any Rem chat attachment with `rem.attachment`. */
    val attachmentFiles: Boolean = false,
    /** The desktop sends result cards, progress and source previews (`media.preview`). */
    val richResults: Boolean = false,
    /** Voice tidying is on in desktop Settings, so dictations go through `rem.tidy`. */
    val voiceTidy: Boolean = false,
    /** The desktop takes `rem.run.retry_review`. */
    val reviewRetry: Boolean = false,
    /** The desktop takes `rem.run.pause` and `rem.run.resume`. */
    val runPause: Boolean = false,
    /** The desktop answers `rem.decisions` and takes `rem.decision.change`. */
    val remDecisions: Boolean = false,
    /** The desktop answers `rem.draft` with Rem's reply as it is written. */
    val draftDeltas: Boolean = false,
    /** The desktop accepts thread.manage `read` and `unread`. */
    val threadRead: Boolean = false,
    /** The desktop takes thread.manage `delete_permanently`. */
    val threadPurge: Boolean = false,
    /** The desktop answers `provider.accounts` and takes an `account` with thread creation and settings. */
    val threadAccounts: Boolean = false,
    /** The desktop takes `provider.account.sign_in`; needs [threadAccounts]. */
    val accountSignIn: Boolean = false,
    /** The desktop answers `rem.notices`, the read behind Rem's chat alerts. */
    val remNotices: Boolean = false,
    /** `rem.notices` takes `include` and `mark`. An older desktop must never be sent them. */
    val remNoticesFilter: Boolean = false,
    /** `rem.notices` takes the `workflow` switch; needs [remNoticesFilter]. */
    val remNoticesWorkflow: Boolean = false,
    /** The desktop shows workflows to the phone. */
    val workflows: Boolean = false,
    /** The desktop answers `workflows.runs`. */
    val workflowRuns: Boolean = false,
    /** The desktop lists triggers and takes `workflows.trigger` and `workflows.triggers.pause`. */
    val workflowTriggers: Boolean = false,
    /** The desktop shows each trigger's approval and takes workflows.trigger.changes and .approve. */
    val workflowTriggerApproval: Boolean = false,
    /** The desktop answers `workflows.failure`. */
    val workflowFailures: Boolean = false,
    /** The desktop takes Try another model on a workflow's tests. */
    val workflowTests: Boolean = false,
    /** The desktop answers `source_control.list` and `source_control.read`. */
    val sourceControl: Boolean = false,
    /** The desktop also answers `source_control.history`, `.commit` and `.diff`. */
    val sourceControlHistory: Boolean = false,
    /** The desktop answers `files.list` and `files.text` and takes `files.open`. */
    val fileExplorer: Boolean = false,
    /** The desktop answers `developer.log`. */
    val developerLog: Boolean = false,
    /** The desktop answers `browser.tabs` and takes `browser.open`. */
    val browser: Boolean = false,
    /** The desktop answers `posts.list` and takes `posts.decide`. */
    val posts: Boolean = false,
    /** The desktop answers `computers.list` and takes `computers.set`. */
    val computers: Boolean = false,
    /** The desktop answers `computers.placement` and takes `computers.place`. */
    val computerPlacement: Boolean = false,
    /** The desktop takes Run there (`computers.run_there`). */
    val runThere: Boolean = false,
    /** The desktop takes the five-section Organizations actions. */
    val organizationsV2: Boolean = false,
    /** The desktop takes `organizations.schedule`; needs [organizationsV2]. */
    val organizationSchedule: Boolean = false,
    /** The desktop takes `organizations.backlog_pull`; needs [organizationsV2]. */
    val organizationBacklogPull: Boolean = false,
    /** The desktop takes `organizations.requests`; needs [organizationsV2]. */
    val organizationRequests: Boolean = false,
    /** The desktop takes `organizations.auto_mode`; needs [organizationsV2]. */
    val organizationAutoMode: Boolean = false,
    /** The desktop has the global board and Rem's self-work. */
    val kanban: Boolean = false,
    /** The board lists its people and the desktop takes `kanban.participant`; needs [kanban]. */
    val kanbanParticipants: Boolean = false,
    /** The desktop keeps each ticket's history and takes comments; needs [kanban]. */
    val kanbanComments: Boolean = false,
) {
    companion object {
        /** Nothing reported yet this session. */
        val NONE = DesktopFeatures()

        /** What [overview] reports, with each extension that builds on another off unless both are on. */
        fun from(overview: DesktopOverview) = DesktopFeatures(
            media = overview.media,
            fileLinks = overview.fileLinks,
            attachmentFiles = overview.attachmentFiles,
            richResults = overview.richResults,
            voiceTidy = overview.voiceTidy,
            reviewRetry = overview.reviewRetry,
            runPause = overview.runPause,
            remDecisions = overview.remDecisions,
            draftDeltas = overview.draftDeltas,
            threadRead = overview.threadRead,
            threadPurge = overview.threadPurge,
            threadAccounts = overview.threadAccounts,
            accountSignIn = overview.threadAccounts && overview.accountSignIn,
            remNotices = overview.remNotices,
            remNoticesFilter = overview.remNoticesFilter,
            remNoticesWorkflow = overview.remNoticesFilter && overview.remNoticesWorkflow,
            workflows = overview.workflows,
            workflowRuns = overview.workflowRuns,
            workflowTriggers = overview.workflowTriggers,
            workflowTriggerApproval = overview.workflowTriggerApproval,
            workflowFailures = overview.workflowFailures,
            workflowTests = overview.workflowTests,
            sourceControl = overview.sourceControl,
            sourceControlHistory = overview.sourceControlHistory,
            fileExplorer = overview.fileExplorer,
            developerLog = overview.developerLog,
            browser = overview.browser,
            posts = overview.posts,
            computers = overview.computers,
            computerPlacement = overview.computerPlacement,
            runThere = overview.runThere,
            organizationsV2 = overview.organizationsV2,
            organizationSchedule = overview.organizationsV2 && overview.organizationSchedule,
            organizationBacklogPull = overview.organizationsV2 && overview.organizationBacklogPull,
            organizationRequests = overview.organizationsV2 && overview.organizationRequests,
            organizationAutoMode = overview.organizationsV2 && overview.organizationAutoMode,
            kanban = overview.kanban,
            kanbanParticipants = overview.kanban && overview.kanbanParticipants,
            kanbanComments = overview.kanban && overview.kanbanComments,
        )
    }
}
