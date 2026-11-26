package dev.raticode.mobile.live

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.lang.reflect.Modifier

/** What the phone may send this session comes only from the overview, through [DesktopFeatures.from]. */
class DesktopFeaturesTest {
    private val nothing = DesktopOverview(emptyList(), rem = false, organizations = false, swarms = false, settings = false, usage = false)

    /** Each field, read with Java reflection so a field added later without a row here fails [everyFieldHasARow]. */
    private val fields = DesktopFeatures::class.java.declaredFields
        .filter { !Modifier.isStatic(it.modifiers) && it.type == java.lang.Boolean.TYPE }
        .onEach { it.isAccessible = true }

    private fun on(features: DesktopFeatures) = fields.filter { it.getBoolean(features) }.map { it.name }.toSet()

    /** For each field: the overview that reports it, and the fields that overview turns on. */
    private val rows: Map<String, Pair<DesktopOverview, Set<String>>> = mapOf(
        "media" to (nothing.copy(media = true) to setOf("media")),
        "fileLinks" to (nothing.copy(fileLinks = true) to setOf("fileLinks")),
        "attachmentFiles" to (nothing.copy(attachmentFiles = true) to setOf("attachmentFiles")),
        "richResults" to (nothing.copy(richResults = true) to setOf("richResults")),
        "voiceTidy" to (nothing.copy(voiceTidy = true) to setOf("voiceTidy")),
        "reviewRetry" to (nothing.copy(reviewRetry = true) to setOf("reviewRetry")),
        "runPause" to (nothing.copy(runPause = true) to setOf("runPause")),
        "remDecisions" to (nothing.copy(remDecisions = true) to setOf("remDecisions")),
        "draftDeltas" to (nothing.copy(draftDeltas = true) to setOf("draftDeltas")),
        "threadRead" to (nothing.copy(threadRead = true) to setOf("threadRead")),
        "threadPurge" to (nothing.copy(threadPurge = true) to setOf("threadPurge")),
        "threadAccounts" to (nothing.copy(threadAccounts = true) to setOf("threadAccounts")),
        "accountSignIn" to (nothing.copy(threadAccounts = true, accountSignIn = true) to setOf("threadAccounts", "accountSignIn")),
        "remNotices" to (nothing.copy(remNotices = true) to setOf("remNotices")),
        "remNoticesFilter" to (nothing.copy(remNoticesFilter = true) to setOf("remNoticesFilter")),
        "remNoticesWorkflow" to (nothing.copy(remNoticesFilter = true, remNoticesWorkflow = true) to setOf("remNoticesFilter", "remNoticesWorkflow")),
        "workflows" to (nothing.copy(workflows = true) to setOf("workflows")),
        "workflowRuns" to (nothing.copy(workflowRuns = true) to setOf("workflowRuns")),
        "workflowTriggers" to (nothing.copy(workflowTriggers = true) to setOf("workflowTriggers")),
        "workflowTriggerApproval" to (nothing.copy(workflowTriggerApproval = true) to setOf("workflowTriggerApproval")),
        "workflowFailures" to (nothing.copy(workflowFailures = true) to setOf("workflowFailures")),
        "workflowTests" to (nothing.copy(workflowTests = true) to setOf("workflowTests")),
        "sourceControl" to (nothing.copy(sourceControl = true) to setOf("sourceControl")),
        "sourceControlHistory" to (nothing.copy(sourceControlHistory = true) to setOf("sourceControlHistory")),
        "fileExplorer" to (nothing.copy(fileExplorer = true) to setOf("fileExplorer")),
        "developerLog" to (nothing.copy(developerLog = true) to setOf("developerLog")),
        "browser" to (nothing.copy(browser = true) to setOf("browser")),
        "posts" to (nothing.copy(posts = true) to setOf("posts")),
        "computers" to (nothing.copy(computers = true) to setOf("computers")),
        "computerPlacement" to (nothing.copy(computerPlacement = true) to setOf("computerPlacement")),
        "runThere" to (nothing.copy(runThere = true) to setOf("runThere")),
        "organizationsV2" to (nothing.copy(organizationsV2 = true) to setOf("organizationsV2")),
        "organizationSchedule" to (nothing.copy(organizationsV2 = true, organizationSchedule = true) to setOf("organizationsV2", "organizationSchedule")),
        "organizationBacklogPull" to (nothing.copy(organizationsV2 = true, organizationBacklogPull = true) to setOf("organizationsV2", "organizationBacklogPull")),
        "organizationRequests" to (nothing.copy(organizationsV2 = true, organizationRequests = true) to setOf("organizationsV2", "organizationRequests")),
        "organizationAutoMode" to (nothing.copy(organizationsV2 = true, organizationAutoMode = true) to setOf("organizationsV2", "organizationAutoMode")),
        "kanban" to (nothing.copy(kanban = true) to setOf("kanban")),
        "kanbanParticipants" to (nothing.copy(kanban = true, kanbanParticipants = true) to setOf("kanban", "kanbanParticipants")),
        "kanbanComments" to (nothing.copy(kanban = true, kanbanComments = true) to setOf("kanban", "kanbanComments")),
    )

    @Test fun everyFieldHasARow() {
        assertEquals(39, fields.size)
        assertEquals(fields.map { it.name }.toSet(), rows.keys)
    }

    @Test fun eachCapabilityTurnsOnItsOwnFieldAndNoOther() {
        for ((name, row) in rows) assertEquals(name, row.second, on(DesktopFeatures.from(row.first)))
    }

    @Test fun anExtensionWithoutWhatItBuildsOnStaysOff() {
        val alone = nothing.copy(accountSignIn = true, remNoticesWorkflow = true, organizationSchedule = true, organizationBacklogPull = true,
            organizationRequests = true, organizationAutoMode = true, kanbanParticipants = true, kanbanComments = true)
        assertEquals(emptySet<String>(), on(DesktopFeatures.from(alone)))
    }

    @Test fun aDesktopReportingEverythingTurnsEveryFieldOn() {
        val all = rows.values.fold(nothing) { overview, row -> merge(overview, row.first) }
        assertEquals(fields.map { it.name }.toSet(), on(DesktopFeatures.from(all)))
    }

    @Test fun noneHasEveryFieldOffAndAnOverviewWithNothingMatchesIt() {
        assertEquals(emptySet<String>(), on(DesktopFeatures.NONE))
        assertEquals(DesktopFeatures.NONE, DesktopFeatures.from(nothing))
        assertEquals(DesktopFeatures.NONE, DesktopFeatures.from(DesktopViews.parse("overview", JSONObject()) as DesktopOverview))
    }

    @Test fun theParsedOverviewFeedsTheSameRules() {
        val json = JSONObject().put("organizations_v2", true).put("organization_auto_mode", true).put("kanban", true).put("browser", true)
        val features = DesktopFeatures.from(DesktopViews.parse("overview", json) as DesktopOverview)
        assertEquals(setOf("organizationsV2", "organizationAutoMode", "kanban", "browser"), on(features))
        assertTrue(DesktopFeatures.from(DesktopViews.parse("overview", JSONObject().put("organization_auto_mode", true)) as DesktopOverview) == DesktopFeatures.NONE)
    }

    /** [a] with every Boolean that is on in [b] turned on too. */
    private fun merge(a: DesktopOverview, b: DesktopOverview): DesktopOverview {
        val copy = a.copy()
        for (field in DesktopOverview::class.java.declaredFields) {
            if (Modifier.isStatic(field.modifiers) || field.type != java.lang.Boolean.TYPE) continue
            field.isAccessible = true
            if (field.getBoolean(b)) field.setBoolean(copy, true)
        }
        return copy
    }
}
