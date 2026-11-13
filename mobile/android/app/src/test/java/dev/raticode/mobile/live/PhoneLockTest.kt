package dev.raticode.mobile.live

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PhoneLockTest {
    // The sentence the desktop sends (src/gofer/devices/phone_guard.py LOCKED_DETAIL).
    private val locked = "This phone is locked. Your computer flagged a request from it as suspicious and stopped it before Rem saw it."

    @Test fun aLockedRefusalLocksThePhone() {
        assertEquals(true, PhoneLock.state("error", JSONObject().put("code", "unauthorized").put("retryable", false).put("detail", locked)))
        assertEquals(true, PhoneLock.state("desktop.result", JSONObject().put("ok", false).put("code", "unauthorized").put("detail", locked)))
    }

    @Test fun otherRefusalsLeaveTheStateAlone() {
        assertNull(PhoneLock.state("error", JSONObject().put("code", "unauthorized").put("detail", "Turn on Use Raticode from this phone.")))
        assertNull(PhoneLock.state("error", JSONObject().put("code", "internal").put("detail", locked)))
        assertNull(PhoneLock.state("chat.message", JSONObject().put("text", locked)))
    }

    @Test fun anAcceptedRequestShowsThePhoneWasUnlocked() {
        assertEquals(false, PhoneLock.state("desktop.result", JSONObject().put("ok", true)))
        assertEquals(false, PhoneLock.state("sync.snapshot", JSONObject()))
        assertEquals(false, PhoneLock.state("chat.accepted", JSONObject()))
    }
}
