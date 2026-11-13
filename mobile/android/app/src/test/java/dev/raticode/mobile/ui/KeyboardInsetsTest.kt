package dev.raticode.mobile.ui

import org.junit.Assert.assertEquals
import org.junit.Test

/** The keyboard is counted once, whether or not the phone also shrinks the window above it. */
class KeyboardInsetsTest {
    // The user's Galaxy screenshot: a 2400 px window and a keyboard (with the navigation bar under it) 935 px tall.
    private val window = 2400
    private val keyboard = 935

    @Test fun aWindowThatReachesTheBottomPadsForTheWholeKeyboard() {
        assertEquals(keyboard, KeyboardInsets.bottom(keyboard, window, viewBottom = window))
    }

    @Test fun aWindowAlreadyShrunkAboveTheKeyboardPadsNothingMore() {
        assertEquals(0, KeyboardInsets.bottom(keyboard, window, viewBottom = window - keyboard))
    }

    @Test fun aPartlyShrunkWindowPadsOnlyWhatIsStillCovered() {
        assertEquals(135, KeyboardInsets.bottom(keyboard, window, viewBottom = window - 800))
    }

    @Test fun withoutAKeyboardTheNavigationBarStillPads() {
        assertEquals(63, KeyboardInsets.bottom(63, window, viewBottom = window))
        assertEquals(0, KeyboardInsets.bottom(0, window, viewBottom = window))
    }

    @Test fun onlyTheBrowserMakesRoomInOneStep() {
        // A web page scrolls its focused field into view when it first gets shorter; the rest of a slide would cover it.
        org.junit.Assert.assertTrue(KeyboardInsets.resizesOnce("Browser"))
        // Raticode's own screens follow the keyboard as it slides.
        for (page in listOf("Rem", "Threads", "Chat", "Files", "Settings")) org.junit.Assert.assertFalse(page, KeyboardInsets.resizesOnce(page))
    }

    @Test fun aViewReachingPastTheWindowIsNotPaddedTwice() {
        assertEquals(keyboard, KeyboardInsets.bottom(keyboard, window, viewBottom = window + 40))
    }
}
