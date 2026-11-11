package dev.raticode.mobile.voice

import android.text.InputType
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ScreenTextTest {
    @Test fun passwordAndPinFieldsAreNeverRead() {
        val text = InputType.TYPE_CLASS_TEXT
        listOf(InputType.TYPE_TEXT_VARIATION_PASSWORD, InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD, InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD)
            .forEach { assertTrue(ScreenText.secret(text or it)) }
        assertTrue(ScreenText.secret(InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_VARIATION_PASSWORD))
        assertFalse(ScreenText.secret(text or InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS))
        assertFalse(ScreenText.secret(InputType.TYPE_CLASS_NUMBER))
        assertFalse(ScreenText.secret(InputType.TYPE_NULL))
    }

    @Test fun textIsTidiedAndRepeatsDropped() {
        val lines = listOf("  Squash   soup ", "Squash soup", "", "Serves\t4\nRoast at 425F", "Pay\u200b now\u202e", "\u0007")
        assertEquals("Squash soup\nServes 4\nRoast at 425F\nPay now", ScreenText.clean(lines))
    }

    @Test fun longScreensStopAtALineWithAMark() {
        val lines = (1..100).map { "Line $it of the article" }
        val text = ScreenText.clean(lines, max = 60)
        assertTrue(text.length <= 60)
        assertTrue(text.endsWith("…"))
        assertEquals("Line 1 of the article", text.lines().first())
        // One enormous line is cut rather than dropped.
        assertEquals(10, ScreenText.clean(listOf("x".repeat(500)), max = 10).length)
    }

    @Test fun oldScreensAreNotSent() {
        val screen = ScreenContext("Chrome", "hi", at = 1_000)
        assertTrue(ScreenText.fresh(screen, 1_000 + ScreenText.MAX_AGE_MS))
        assertFalse(ScreenText.fresh(screen, 1_001 + ScreenText.MAX_AGE_MS))
        assertFalse("A clock that went backwards isn't trusted", ScreenText.fresh(screen, 999))
    }
}
