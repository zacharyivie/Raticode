package dev.raticode.mobile

import androidx.activity.compose.setContent
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.assertIsNotSelected
import androidx.compose.ui.test.assertIsSelected
import androidx.compose.ui.test.hasAnyAncestor
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import dev.raticode.mobile.ui.RaticodeBottomBar
import dev.raticode.mobile.ui.RaticodeBottomItem
import dev.raticode.mobile.ui.RaticodeTheme
import dev.raticode.mobile.ui.ThreadIcons
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/** The bottom bar is as tall as YouTube's, every item is still a full touch target, and it grows for large text. */
class BottomBarLayoutTest {
    @get:Rule val compose = createAndroidComposeRule<MainActivity>()
    private var page by mutableStateOf("Workflows")
    private val names = listOf("Rem", "Threads", "Workflows", "Browser", "More")

    private fun show(fontScale: Float) = compose.runOnUiThread {
        compose.activity.setContent { RaticodeTheme {
            val density = LocalDensity.current
            CompositionLocalProvider(LocalDensity provides Density(density.density, fontScale)) {
                RaticodeBottomBar(Modifier.testTag("bar")) {
                    names.forEach { RaticodeBottomItem(page == it, { page = it }, it, "Open $it", ThreadIcons.Browser, badge = "3".takeIf { _ -> it == "More" }) }
                }
            }
        } }
    }
    private fun item(name: String) = compose.onNode(hasContentDescription("Open $name") and hasAnyAncestor(hasTestTag("bar")))

    @Test fun shortBarWithFullTouchTargets() {
        show(1f)
        compose.waitForIdle()
        val height = compose.onNodeWithTag("bar").fetchSemanticsNode().boundsInRoot.height
        assertEquals("56dp bar", with(compose.density) { 56.dp.toPx() }, height, 1f)
        val touch = with(compose.density) { 48.dp.toPx() }
        val items = compose.onAllNodes(hasClickAction() and hasAnyAncestor(hasTestTag("bar"))).fetchSemanticsNodes()
        assertEquals(5, items.size)
        items.forEach { assertTrue("${it.boundsInRoot} is a full target", it.boundsInRoot.height >= touch - 1 && it.boundsInRoot.width >= touch - 1) }

        item("Workflows").assertIsSelected()
        item("Browser").assertIsNotSelected().performClick()
        compose.waitForIdle()
        item("Browser").assertIsSelected()
        item("Workflows").assertIsNotSelected()
    }

    @Test fun growsForLargeTextInsteadOfCuttingItOff() {
        show(2f)
        compose.waitForIdle()
        val height = compose.onNodeWithTag("bar").fetchSemanticsNode().boundsInRoot.height
        assertTrue("Taller than 56dp at double text size: $height", height > with(compose.density) { 56.dp.toPx() })
    }
}
