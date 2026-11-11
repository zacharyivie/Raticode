package dev.raticode.mobile.protocol.v2

import org.junit.Assert.*
import org.junit.Test

class MobileCapabilitiesTest {
    private fun rejects(block: () -> Unit) {
        var rejected = false
        try { block() } catch (_: IllegalArgumentException) { rejected = true }
        assertTrue(rejected)
    }
    @Test fun cannotBecomeWorkerOrAcceptAssignments() {
        rejects { MobileCapabilities.validateGrant("desktop", setOf("worker.execute"), false) }
        rejects { MobileCapabilities.validateGrant("controller", setOf("worker.execute"), false) }
        for (type in listOf("job.submit", "job.cancel", "job.get")) rejects { MobileCapabilities.validateIncomingType(type) }
    }
    @Test fun approvalRequiresPolicyAndOrdinaryGrantsRemainPossible() {
        rejects { MobileCapabilities.validateGrant("controller", setOf("remote_approval"), false) }
        MobileCapabilities.validateGrant("controller", setOf("chat", "status"), false)
    }
}
