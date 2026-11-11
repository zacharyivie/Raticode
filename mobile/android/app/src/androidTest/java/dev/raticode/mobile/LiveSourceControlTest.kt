package dev.raticode.mobile

import android.app.Application
import android.content.Context
import androidx.activity.compose.setContent
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.lifecycle.ViewModelStore
import androidx.test.platform.app.InstrumentationRegistry
import dev.raticode.mobile.live.*
import dev.raticode.mobile.ui.LiveNavigation
import dev.raticode.mobile.ui.LiveScreen
import dev.raticode.mobile.ui.NavBarPrefs
import dev.raticode.mobile.ui.RaticodeTheme
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import java.io.File

/**
 * The configurable bottom bar and the Source control page against `live-device-fixture.py --workspace --remote --desktop --git`,
 * which runs the desktop's real device application and Git reads on a disposable repository ("Fixture repo") and a plain
 * folder ("Fixture project"). [olderDesktopIsAskedToUpdate] needs the same fixture run from a desktop without source control.
 */
class LiveSourceControlTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private val app get() = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
    private val bar = hasAnyAncestor(hasTestTag("desktopNavigation"))

    private fun screenshot(name: String) {
        compose.waitForIdle()
        InstrumentationRegistry.getInstrumentation().uiAutomation.executeShellCommand("screencap -p /data/local/tmp/raticode-$name").use {
            android.os.ParcelFileDescriptor.AutoCloseInputStream(it).readBytes()
        }
    }

    private fun waitFor(what: String, client: LiveClient, timeout: Long = 20000, condition: () -> Boolean) {
        try { compose.waitUntil(timeout, condition) }
        catch (failure: ComposeTimeoutException) {
            throw AssertionError("$what: errors=${client.desktopErrors}; request=${client.requestError}; status=${client.status}; git=${client.sourceControl}", failure)
        }
    }

    private fun back() { compose.runOnUiThread { compose.activity.onBackPressedDispatcher.onBackPressed() }; compose.waitForIdle() }
    private fun barItems() = compose.onAllNodes(hasClickAction() and bar).fetchSemanticsNodes()
        .sortedBy { it.boundsInRoot.left }.map { it.config[androidx.compose.ui.semantics.SemanticsProperties.ContentDescription].first() }
    private fun hasBack() = compose.onAllNodesWithContentDescription("Back").fetchSemanticsNodes().isNotEmpty()

    @Test fun bottomBarChoicesAndSourceControl() {
        val path = InstrumentationRegistry.getArguments().getString("desktopInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop --git", path != null && InstrumentationRegistry.getArguments().getString("olderDesktop") == null)
        compose.runOnUiThread { compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT }
        DeviceStore(app).reset()
        app.getSharedPreferences("navigation", Context.MODE_PRIVATE).edit().clear().commit()
        val owners = ViewModelStore()
        val link = mutableStateOf<String?>(null)
        val generation = mutableIntStateOf(0)
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("git", client)
            compose.activity.setContent { RaticodeTheme { key(generation.intValue) {
                LiveScreen(liveClient = client, initialThread = link.value, consumeInitialThread = { link.value = null })
            } } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop control", client, 30000) { client.connected && client.desktopControl && client.sourceControl == true }
            // The bar starts as it always was, and the app opens to its first destination.
            assertEquals(listOf("Rem", "Threads", "Organizations", "Swarms", "More"), barItems())
            compose.onNode(hasContentDescription("Rem") and bar).assertIsSelected()

            // Source control from More: a back arrow, both projects, and each one's state.
            compose.onNode(hasContentDescription("More") and bar).performClick()
            compose.onNodeWithText("Organizations", substring = false).assertDoesNotExist()
            compose.onNodeWithText("Source control", substring = false).performScrollTo().performClick()
            waitFor("project list", client) { (client.desktopViews[SourceControl.LIST] as? SourceProjectList)?.projects?.size == 2 }
            assertTrue(hasBack())
            compose.onNodeWithText("Fixture repo").assertIsDisplayed()
            compose.onNodeWithText("⎇ main").assertIsDisplayed()
            compose.onNodeWithText("↑ 3 to push").assertIsDisplayed()
            compose.onNodeWithText("● 4 changes").assertIsDisplayed()
            compose.onNodeWithText("Not a Git repository").assertIsDisplayed()
            compose.onNodeWithText("Merge feature/graph", substring = true).assertIsDisplayed()
            screenshot("source-list.png")

            compose.onNodeWithText("Fixture repo").performClick()
            val repo = (client.desktopViews[SourceControl.LIST] as SourceProjectList).projects.first { it.name == "Fixture repo" }
            // A project opens to History, as on desktop; Changes is a tab away.
            waitFor("history", client) { client.desktopViews[SourceControl.historyKey(repo.id)] is SourceHistory }
            compose.onNode(hasText("History", substring = false) and isSelected()).assertIsDisplayed()
            compose.onNodeWithText("Changes · 4").performClick()
            waitFor("project detail", client) { (client.desktopViews[SourceControl.key(repo.id)] as? SourceProject)?.detailed == true }
            compose.onNodeWithText("Staged · 1").assertIsDisplayed()
            compose.onNodeWithText("staged.kt").assertIsDisplayed()
            back()
            compose.onNodeWithText("Fixture project").performClick()
            val plain = (client.desktopViews[SourceControl.LIST] as SourceProjectList).projects.first { it.name == "Fixture project" }
            waitFor("plain folder", client) { (client.desktopViews[SourceControl.key(plain.id)] as? SourceProject)?.state == "not_repository" }
            // A plain folder has no history, so the phone reads its status instead of asking for history.
            assertTrue(client.desktopErrors.keys.none { it.startsWith(SourceControl.HISTORY) })
            screenshot("source-not-repo.png")
            compose.onNodeWithText("There are no changes to track here", substring = true).assertIsDisplayed()
            back(); back()
            compose.onNodeWithTag("moreList").assertIsDisplayed()

            // Replace Orgs and Swarms with Source control and Workflows; the choice is saved as it is made.
            compose.onNodeWithText("Bottom bar", substring = false).performScrollTo().performClick()
            listOf("Organizations", "Swarms", "Source control", "Workflows").forEach {
                compose.onNode(hasText(it, substring = false) and hasAnyAncestor(hasTestTag("navBarSettings"))).performScrollTo().performClick()
            }
            assertEquals(listOf("Rem", "Threads", "SourceControl", "Workflows"), NavBarPrefs.load(app))
            compose.onNodeWithContentDescription("Move Source control up, further left on the bar").performScrollTo().performClick()
            assertEquals(listOf("Rem", "SourceControl", "Threads", "Workflows"), NavBarPrefs.load(app))
            // The bar is full, so the destinations left out can't be added until one leaves.
            compose.onNodeWithText("The bar is full. Remove a destination to add another.").performScrollTo().assertIsDisplayed()
            screenshot("bar-settings.png")
            compose.onNodeWithContentDescription("Back").performClick()
            assertEquals(listOf("Rem", "Source control", "Threads", "Workflows", "More"), barItems())

            // Orgs and Swarms stay in More, and open with a way back to it.
            compose.onNodeWithText("Swarms", substring = false).performScrollTo().assertIsDisplayed()
            compose.onNodeWithText("Organizations", substring = false).performScrollTo().performClick()
            compose.onNodeWithText("AI teams that run on your desktop").assertIsDisplayed()
            compose.onNodeWithContentDescription("Back").performClick()
            compose.onNodeWithTag("moreList").assertIsDisplayed()

            // On the bar, Source control is a top-level page without a back arrow.
            compose.onNode(hasContentDescription("Source control") and bar).performClick()
            waitFor("list again", client) { client.desktopViews[SourceControl.LIST] is SourceProjectList }
            compose.onNodeWithText("Fixture repo").assertIsDisplayed()
            assertFalse(hasBack())
            screenshot("source-on-bar.png")

            // Offline keeps the last status and says so.
            compose.runOnUiThread { client.disconnect() }
            waitFor("offline", client) { !client.connected }
            compose.onNodeWithText("Offline. Showing what your desktop last sent.").assertIsDisplayed()
            compose.onNodeWithText("Fixture repo").assertIsDisplayed()
            compose.onNodeWithContentDescription("Refresh").assertIsNotEnabled()
            screenshot("source-offline.png")
            compose.runOnUiThread { client.connect() }
            waitFor("reconnected", client, 30000) { client.connected && client.sourceControl == true }
            compose.onNodeWithText("Offline. Showing what your desktop last sent.").assertDoesNotExist()

            // A fresh start reads the saved bar and opens its first destination.
            compose.runOnUiThread { generation.intValue++ }
            compose.waitForIdle()
            assertEquals(listOf("Rem", "Source control", "Threads", "Workflows", "More"), barItems())
            compose.onNode(hasContentDescription("Rem") and bar).assertIsSelected()
            // Links still open their own page: approvals, now off the bar, gets a back arrow home.
            compose.runOnUiThread { link.value = "approvals" }
            compose.waitUntil(5000) { compose.onAllNodesWithTag("approvalList").fetchSemanticsNodes().isNotEmpty() }
            assertTrue(hasBack())
            back()
            compose.onNode(hasContentDescription("Rem") and bar).assertIsSelected()
            compose.onNode(hasContentDescription("Source control") and bar).performClick()
            compose.runOnUiThread { link.value = "rem" }
            compose.waitUntil(5000) { compose.onAllNodes(hasContentDescription("Rem") and bar and isSelected()).fetchSemanticsNodes().isNotEmpty() }

            // A bar without Rem: the app opens to its first destination, and Rem opened from More has a back arrow.
            compose.runOnUiThread { NavBarPrefs.save(app, listOf("SourceControl", "Threads")); generation.intValue++ }
            compose.waitForIdle()
            assertEquals(listOf("Source control", "Threads", "More"), barItems())
            compose.onNode(hasContentDescription("Source control") and bar).assertIsSelected()
            compose.onNode(hasContentDescription("More") and bar).performClick()
            compose.onNodeWithText("Rem", substring = false).performScrollTo().performClick()
            compose.onNodeWithTag("moreBack").assertIsDisplayed().performClick()
            compose.onNodeWithTag("moreList").assertIsDisplayed()
            back()
            compose.onNode(hasContentDescription("Source control") and bar).assertIsSelected()
        } finally {
            compose.runOnUiThread { owners.clear() }
            app.getSharedPreferences("navigation", Context.MODE_PRIVATE).edit().clear().commit()
            DeviceStore(app).reset()
        }
    }

    /** Starts paired with Source control on the bar and opens the fixture repository. */
    private fun openRepository(path: String): LiveClient {
        DeviceStore(app).reset()
        NavBarPrefs.save(app, listOf("SourceControl", "Rem"))
        lateinit var client: LiveClient
        compose.runOnUiThread {
            compose.activity.requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
            client = LiveClient(app)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client) } }
            client.preview(File(path).readText()); client.pair()
        }
        waitFor("desktop control", client, 30000) { client.connected && client.desktopControl && client.sourceControl != null }
        waitFor("project list", client) { (client.desktopViews[SourceControl.LIST] as? SourceProjectList)?.projects?.size == 2 }
        compose.onNodeWithText("Fixture repo").performClick()
        val repo = (client.desktopViews[SourceControl.LIST] as SourceProjectList).projects.first { it.name == "Fixture repo" }
        // History first on a desktop that sends it, Changes on one that doesn't.
        waitFor("project opened", client) { client.desktopViews[SourceControl.historyKey(repo.id)] is SourceHistory
            || (client.desktopViews[SourceControl.key(repo.id)] as? SourceProject)?.detailed == true }
        return client
    }

    private fun graphImage() = compose.onAllNodesWithTag("historyRow").onFirst().captureToImage().asAndroidBitmap().let { bitmap ->
        // The right third of the first row is its graph cell.
        IntArray(bitmap.width / 3 * bitmap.height).also { bitmap.getPixels(it, 0, bitmap.width / 3, bitmap.width - bitmap.width / 3, 0, bitmap.width / 3, bitmap.height) }
    }

    /** The desktop side pane on a phone: changes and diffs, a refused secret, the branch graph and its filters, a commit, branches. */
    @Test fun sourceControlPaneOnThePhone() {
        val path = InstrumentationRegistry.getArguments().getString("desktopInvitationFile")
        assumeTrue("Requires disposable fixture with --workspace --remote --desktop --git", path != null && InstrumentationRegistry.getArguments().getString("olderDesktop") == null)
        lateinit var client: LiveClient
        try {
            client = openRepository(path!!)
            assertTrue(client.sourceControlHistory == true)
            compose.onNode(hasText("History", substring = false) and isSelected()).assertIsDisplayed()
            compose.onNodeWithText("Drag the graph sideways", substring = true).assertIsDisplayed()
            // Changes: grouped like the desktop panel, each file by name and folder.
            compose.onNodeWithText("Changes · 4").performClick()
            val repo = (client.desktopViews[SourceControl.LIST] as SourceProjectList).projects.first { it.name == "Fixture repo" }
            waitFor("project detail", client) { (client.desktopViews[SourceControl.key(repo.id)] as? SourceProject)?.detailed == true }
            compose.onNodeWithText("Staged · 1").assertIsDisplayed()
            compose.onNodeWithText("Changes · 3").assertIsDisplayed()
            screenshot("pane-changes.png")
            compose.onNodeWithContentDescription("Modified: app.kt").performClick()
            waitFor("diff", client) { client.desktopViews.values.any { it is SourceDiff && it.path == "app.kt" } }
            compose.onNodeWithContentDescription("Added: fun main() { println() }").assertIsDisplayed()
            compose.onNodeWithContentDescription("Removed: fun main() {}").assertIsDisplayed()
            screenshot("pane-diff.png")
            // Ask Rem fills the message box with the file and project, sends nothing, and coming back finds the same diff.
            val sentBefore = client.messages.size
            compose.onNodeWithTag("askRemHunk").performClick()
            compose.waitUntil(10000) { compose.onAllNodesWithTag("remComposer").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithTag("remComposer").assert(hasText("About this change: app.kt", substring = true))
            compose.onNodeWithTag("remComposer").assert(hasText("Project: Fixture repo", substring = true))
            assertEquals("Nothing is sent by Ask Rem", sentBefore, client.messages.size)
            // The message box took the keyboard, which covers the bottom bar until it closes.
            compose.runOnUiThread { androidx.core.view.WindowCompat.getInsetsController(compose.activity.window, compose.activity.window.decorView)
                .hide(androidx.core.view.WindowInsetsCompat.Type.ime()) }
            waitFor("bottom bar", client) { compose.onAllNodes(hasContentDescription("Source control") and bar).fetchSemanticsNodes().isNotEmpty() }
            compose.onNode(hasContentDescription("Source control") and bar).performClick()
            waitFor("same diff", client) { compose.onAllNodesWithTag("sourceDiff").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("Added: fun main() { println() }").assertIsDisplayed()
            back()
            // A secret-looking file is listed but its contents stay on the computer, and it offers no Ask Rem.
            compose.onNodeWithTag("sourceChanges").performScrollToNode(hasContentDescription("New: .env"))
            compose.onNodeWithContentDescription("New: .env").performClick()
            waitFor("refusal", client) { client.desktopErrors.keys.any { it.startsWith(SourceControl.DIFF) } }
            compose.onNodeWithText("This file may hold credentials", substring = true).assertIsDisplayed()
            compose.onNodeWithText("Try again").assertIsDisplayed()
            compose.onAllNodes(hasText("Ask Rem", substring = true)).assertCountEquals(0)
            screenshot("pane-refused.png")
            back()

            // History: the graph, panned sideways when it's wider than its third of the screen.
            compose.onNodeWithText("History", substring = false).performClick()
            waitFor("history", client) { client.desktopViews.values.any { it is SourceHistory } }
            compose.onNodeWithText("Drag the graph sideways", substring = true).assertIsDisplayed()
            screenshot("pane-history.png")
            // Each row shows its added and removed lines right after its ID, without opening it, and clear of the graph.
            val ids = compose.onAllNodesWithTag("commitId", useUnmergedTree = true).fetchSemanticsNodes()
            val stats = compose.onAllNodesWithTag("commitStats", useUnmergedTree = true).fetchSemanticsNodes()
            val rows = compose.onAllNodesWithTag("historyRow").fetchSemanticsNodes()
            assertTrue(ids.isNotEmpty() && ids.size == stats.size)
            ids.zip(stats).forEach { (id, count) ->
                assertTrue("Counts sit inside the ID group, after the ID", count.boundsInRoot.left > id.boundsInRoot.left + 8 && count.boundsInRoot.right <= id.boundsInRoot.right + 1)
                assertTrue("Counts share the ID's line", kotlin.math.abs(count.boundsInRoot.center.y - id.boundsInRoot.center.y) < 4)
            }
            val row = rows.first().boundsInRoot
            assertTrue("Counts stay in the text's two thirds", stats.first().boundsInRoot.right <= row.left + row.width * 2 / 3 + 2)
            // An octopus merge counts against its first parent, and the row says so to screen readers.
            compose.onNodeWithTag("sourceHistory").performScrollToNode(hasText("Try every idea together", substring = true))
            compose.onNode(hasContentDescription("Try every idea together", substring = true)
                and hasContentDescription("6 insertions, 0 deletions, compared with the first parent", substring = true)).assertIsDisplayed()
            compose.onNodeWithText("+6", useUnmergedTree = true).assertIsDisplayed()
            // A binary-only commit shows its binary count and never +0 −0.
            compose.onNodeWithTag("sourceHistory").performScrollToNode(hasText("Add the app icon", substring = true))
            compose.onNode(hasContentDescription("Add the app icon", substring = true)
                and hasContentDescription("0 insertions, 0 deletions, 1 binary file without line counts", substring = true)).assertIsDisplayed()
            compose.onNode(hasText("1 binary") and hasAnyAncestor(hasContentDescription("Add the app icon", substring = true)), useUnmergedTree = true).assertIsDisplayed()
            compose.onAllNodes(hasText("+0") and hasAnyAncestor(hasContentDescription("Add the app icon", substring = true)), useUnmergedTree = true).assertCountEquals(0)
            screenshot("pane-history-counts.png")
            compose.onNodeWithTag("sourceHistory").performScrollToIndex(0)
            val before = graphImage()
            compose.onAllNodesWithTag("historyRow").onFirst().performTouchInput { swipeLeft(startX = right - 4f, endX = right - width / 3f + 4f) }
            compose.waitForIdle()
            assertFalse("The graph moved", before.contentEquals(graphImage()))
            screenshot("pane-history-panned.png")
            compose.onNodeWithTag("sourceHistory").performScrollToNode(hasText("Merge feature/graph"))
            compose.onNodeWithText("⌂ v0.1").assertIsDisplayed()
            screenshot("pane-history-merge.png")
            compose.onNodeWithTag("sourceHistory").performScrollToIndex(0)
            // One branch, then a search, as the desktop's filters do.
            compose.onNodeWithContentDescription("History branches: All branches and tags").performClick()
            compose.onNodeWithText("feature/wip", substring = false).performClick()
            waitFor("one branch", client) { client.desktopViews.values.filterIsInstance<SourceHistory>().any { h -> h.commits.none { it.subject.startsWith("Try idea") } } }
            compose.onNodeWithText("Sketch the phone layout").assertIsDisplayed()
            compose.onNodeWithTag("historySearch").performTextInput("graph")
            compose.onNodeWithTag("historySearch").performImeAction()
            compose.waitForIdle()
            compose.onNodeWithTag("sourceHistory").performScrollToNode(hasText("Draw the branch graph"))
            compose.onNodeWithText("Draw the branch graph").assertIsDisplayed()
            compose.onNodeWithText("Sketch the phone layout").assertDoesNotExist()
            screenshot("pane-history-filtered.png")
            // A commit's files, and a file's change in it.
            compose.onNodeWithText("Merge feature/graph").performClick()
            waitFor("commit", client) { client.desktopViews.values.any { it is SourceCommitDetail } }
            compose.onNodeWithText("Files · 1").assertIsDisplayed()
            compose.onNodeWithText("A merge", substring = true).assertIsDisplayed()
            compose.onNodeWithText("Ask Rem about this commit").assertIsDisplayed()
            screenshot("pane-commit.png")
            compose.onNodeWithContentDescription("Added: graph.kt, +1 −0").performClick()
            waitFor("commit diff", client) { client.desktopViews.values.any { it is SourceDiff && it.source == "commit" } }
            compose.onNodeWithContentDescription("Added: val lanes = 4").assertIsDisplayed()
            back(); back()

            // Branches: current, checked out in a worktree, tracking, and the worktrees.
            compose.onNodeWithText("Branches", substring = false).performClick()
            waitFor("branches", client) { compose.onAllNodesWithTag("sourceBranches").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithText("3 to push · origin/main").assertIsDisplayed()
            compose.onNodeWithTag("sourceBranches").performScrollToNode(hasText(".worktrees/bar"))
            compose.onNodeWithText("feature/bar · no changes").assertIsDisplayed()
            screenshot("pane-branches.png")
            back()
            compose.onNodeWithTag("sourceProjects").assertIsDisplayed()
        } finally {
            app.getSharedPreferences("navigation", Context.MODE_PRIVATE).edit().clear().commit()
            DeviceStore(app).reset()
        }
    }

    /** A desktop with the first source control reads but not history: the status works and history says to update. */
    @Test fun historyNeedsANewerDesktop() {
        val path = InstrumentationRegistry.getArguments().getString("desktopInvitationFile")
        assumeTrue("Requires a fixture run from desktop 41d5fc3 with -e olderDesktop history", path != null && InstrumentationRegistry.getArguments().getString("olderDesktop") == "history")
        lateinit var client: LiveClient
        try {
            client = openRepository(path!!)
            assertTrue(client.sourceControlHistory == false)
            // Without history support the project opens to Changes, not to a request to update.
            compose.onNode(hasText("Changes", substring = true) and isSelected()).assertIsDisplayed()
            compose.onNodeWithText("Staged · 1").assertIsDisplayed()
            compose.onNodeWithText("History", substring = false).performClick()
            compose.onNodeWithText(SourceControl.UPDATE_FOR_HISTORY).assertIsDisplayed()
            screenshot("pane-history-older.png")
            Thread.sleep(4000)
            assertTrue(client.connected)
            assertTrue(client.desktopErrors.keys.none { it.startsWith(SourceControl.HISTORY) })
        } finally {
            app.getSharedPreferences("navigation", Context.MODE_PRIVATE).edit().clear().commit()
            DeviceStore(app).reset()
        }
    }

    /** Against a desktop without source control: the phone never sends the reads and says to update the desktop. */
    @Test fun olderDesktopIsAskedToUpdate() {
        val path = InstrumentationRegistry.getArguments().getString("desktopInvitationFile")
        assumeTrue("Requires an older desktop fixture with --workspace --remote --desktop", path != null && InstrumentationRegistry.getArguments().getString("olderDesktop") == "true")
        DeviceStore(app).reset()
        app.getSharedPreferences("navigation", Context.MODE_PRIVATE).edit().clear().commit()
        NavBarPrefs.save(app, listOf("Rem"))
        val owners = ViewModelStore()
        lateinit var client: LiveClient
        compose.runOnUiThread {
            client = LiveClient(app)
            owners.put("old", client)
            compose.activity.setContent { RaticodeTheme { LiveScreen(liveClient = client) } }
            client.preview(File(path!!).readText()); client.pair()
        }
        try {
            waitFor("desktop control", client, 30000) { client.connected && client.desktopControl && client.sourceControl == false }
            assertEquals(listOf("Rem", "More"), barItems())
            compose.onNode(hasContentDescription("More") and bar).performClick()
            compose.onNodeWithText("Update Raticode on your computer to use source control here").performScrollTo().assertIsDisplayed()
            compose.onNodeWithText("Source control", substring = false).performScrollTo().performClick()
            compose.onNodeWithText(SourceControl.UPDATE_DESKTOP).assertIsDisplayed()
            compose.onNodeWithText("Check again").assertIsDisplayed()
            screenshot("source-older-desktop.png")
            // The connection stays up: no unknown op reached the older desktop.
            Thread.sleep(6000)
            compose.waitForIdle()
            assertTrue(client.connected)
            assertTrue(client.desktopErrors.keys.none { SourceControl.isOp(it) })
            assertFalse(client.desktopViews.keys.any { SourceControl.isOp(it) })
        } finally {
            compose.runOnUiThread { owners.clear() }
            app.getSharedPreferences("navigation", Context.MODE_PRIVATE).edit().clear().commit()
            DeviceStore(app).reset()
        }
    }
}
