package dev.raticode.mobile

import dev.raticode.mobile.browser.BrowserControlsMotion
import org.junit.rules.ExternalResource

/** Keeps the browser's controls from folding for being idle, for tests that tap them long after a page finished loading. */
class KeepBrowserControls : ExternalResource() {
    private var saved = 0L
    override fun before() { saved = BrowserControlsMotion.idleMillis; BrowserControlsMotion.idleMillis = Long.MAX_VALUE }
    override fun after() { BrowserControlsMotion.idleMillis = saved }
}
