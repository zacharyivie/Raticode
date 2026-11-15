package dev.raticode.mobile.browser

import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Test

/** How an extension's toolbar and page buttons are filled in per tab (see [GeckoRules.ExtensionButtons]). */
class ExtensionButtonsTest {
    /** Stands in for GeckoView's button: a tab's change fills its gaps from an every-tab button of the same kind only. */
    private data class Button(val page: Boolean, val title: String? = null, val badge: String? = null)

    private fun buttons() = GeckoRules.ExtensionButtons<Button> { change, everyTab ->
        require(change.page == everyTab.page) { "defaultValue must be of the same type" }
        Button(change.page, change.title ?: everyTab.title, change.badge ?: everyTab.badge)
    }

    /** The crash in Mobile 0.34.0: a page button arrived for a tab after the toolbar button was set for every tab. */
    @Test fun aTabsPageButtonIsNeverFilledFromTheToolbarButton() {
        val buttons = buttons()
        buttons.changed("ext", page = false, forEveryTab = true, button = Button(page = false, title = "Toolbar", badge = "3"))
        val change = Button(page = true, badge = "1")
        assertSame(change, buttons.changed("ext", page = true, forEveryTab = false, button = change))
    }

    @Test fun eachKindIsFilledFromItsOwnEveryTabButton() {
        val buttons = buttons()
        buttons.changed("ext", page = false, forEveryTab = true, button = Button(page = false, title = "Toolbar", badge = "3"))
        buttons.changed("ext", page = true, forEveryTab = true, button = Button(page = true, title = "Page"))
        assertEquals(Button(page = false, title = "Toolbar", badge = "9"), buttons.changed("ext", page = false, forEveryTab = false, button = Button(page = false, badge = "9")))
        assertEquals(Button(page = true, title = "Page", badge = "1"), buttons.changed("ext", page = true, forEveryTab = false, button = Button(page = true, badge = "1")))
    }

    /** If GeckoView still refuses a fill, the tab's change is shown as it is rather than ending the app. */
    @Test fun aRefusedFillShowsTheChangeAsItIs() {
        val buttons = GeckoRules.ExtensionButtons<Button> { _, _ -> throw IllegalArgumentException("refused") }
        buttons.changed("ext", page = false, forEveryTab = true, button = Button(page = false, title = "Toolbar"))
        val change = Button(page = false, badge = "2")
        assertSame(change, buttons.changed("ext", page = false, forEveryTab = false, button = change))
    }

    @Test fun extensionsAreKeptApartAndForgottenOnRemoval() {
        val buttons = buttons()
        buttons.changed("a", page = false, forEveryTab = true, button = Button(page = false, title = "A"))
        buttons.changed("b", page = false, forEveryTab = true, button = Button(page = false, title = "B"))
        buttons.forget("a")
        val change = Button(page = false, badge = "1")
        assertSame(change, buttons.changed("a", page = false, forEveryTab = false, button = change))
        assertEquals("B", buttons.changed("b", page = false, forEveryTab = false, button = change).title)
    }
}
