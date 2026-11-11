package dev.raticode.mobile.live

import org.junit.Assert.*
import org.junit.Test

class ReplyWatchTest {
    @Test fun aSessionThatStopsAnsweringIsNoticedLongBeforeTcpGivesUp() {
        val watch = ReplyWatch(45_000)
        watch.sent(1_000)
        assertFalse(watch.stalled(45_999))
        assertTrue(watch.stalled(46_000))
    }

    @Test fun anyAnswerClearsTheWatchAndLaterSendsStartANewWait() {
        val watch = ReplyWatch(45_000)
        watch.sent(0)
        watch.sent(40_000) // A second request does not push the deadline back.
        watch.heard()
        assertFalse(watch.stalled(100_000))
        watch.sent(100_000)
        assertFalse(watch.stalled(144_999))
        assertTrue(watch.stalled(145_000))
    }

    @Test fun anIdleSessionWithNothingSentIsNeverStale() {
        assertFalse(ReplyWatch(45_000).stalled(Long.MAX_VALUE / 2))
    }

    @Test fun theLimitStaysWellAboveTheSyncInterval() {
        // The phone asks for a sync every 5 s; a slow relayed reply must not cut a healthy session.
        assertTrue(ReplyWatch.STALL_MILLIS >= 30_000)
        assertTrue(stalledGuidance("Tailscale").contains("over Tailscale"))
        assertTrue(stalledGuidance("Wi-Fi").contains("queued messages stay on this phone"))
    }

    @Test fun tailscaleGetsMoreTimeToConnectThanWifi() {
        assertEquals(4000, connectTimeout(DevicePath.WIFI))
        assertTrue(connectTimeout(DevicePath.TAILSCALE) >= 10_000)
    }
}
