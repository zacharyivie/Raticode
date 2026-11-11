package dev.raticode.mobile.live

import java.io.IOException
import java.net.SocketTimeoutException
import javax.net.ssl.SSLException
import org.junit.Assert.*
import org.junit.Test

class ConnectionFailureTest {
    @Test fun diagnosticsNeverExposeTransportExceptionText() {
        val privateText = "https://relay.invalid/private-topic?secret=private-secret"
        for (stage in ConnectionStage.entries) {
            for (failure in listOf(SSLException(privateText), SocketTimeoutException(privateText), IOException(privateText), IllegalStateException(privateText))) {
                val guidance = connectionGuidance(stage, failure)
                assertFalse(guidance.contains("private-topic"))
                assertFalse(guidance.contains("private-secret"))
            }
        }
    }

    @Test fun unreachableDesktopAndPendingConfirmationHaveDifferentRecovery() {
        val timeout = SocketTimeoutException()
        val unreachable = connectionGuidance(ConnectionStage.AUTHENTICATION, timeout)
        assertTrue(unreachable.contains("networking is responding"))
        assertFalse(unreachable.contains("relay"))
        val pending = connectionGuidance(ConnectionStage.CONFIRMATION, timeout)
        assertTrue(pending.contains("confirm this phone's identity"))
    }

    @Test fun socketTimeoutDoesNotImplyDesktopWasReachedOrIdentityWasRevoked() {
        val timeout = SocketTimeoutException()
        val connecting = connectionGuidance(ConnectionStage.CONNECTING, timeout)
        assertTrue(connecting.contains("Could not reach the desktop over Wi-Fi"))
        assertTrue(connecting.contains("firewall"))
        assertTrue(connecting.contains("Pairing has not reached desktop"))
        assertFalse(connecting.contains("revoked"))
        val authenticating = connectionGuidance(ConnectionStage.AUTHENTICATION, timeout)
        assertTrue(authenticating.contains("Connected over Wi-Fi"))
        assertTrue(authenticating.contains("TLS authentication timed out"))
        assertNotEquals(connecting, authenticating)
    }

    @Test fun missingWifiKeepsItsOwnGuidance() {
        assertEquals(NO_WIFI_GUIDANCE, connectionGuidance(ConnectionStage.CONNECTING, ConnectionFailure(NO_WIFI_GUIDANCE)))
    }

    @Test fun onlyNetworkAndDiskFailuresReconnectAndInterruptionStillStopsTheLoop() {
        assertTrue(reachesSyncLoop(IOException("socket closed")))
        assertTrue(reachesSyncLoop(ConnectionFailure(NO_WIFI_GUIDANCE)))
        assertTrue(reachesSyncLoop(InterruptedException()))
        assertFalse(reachesSyncLoop(IllegalStateException("bug")))
        assertFalse(reachesSyncLoop(org.json.JSONException("bad field")))
        assertFalse(reachesSyncLoop(java.security.GeneralSecurityException("keystore")))
    }

    @Test fun phoneFailuresAreNotBlamedOnWifiAndNeverQuoteTheirText() {
        val privateText = "private draft words"
        for (failure in listOf(IllegalStateException(privateText), java.security.GeneralSecurityException(privateText))) {
            val guidance = commandFailureGuidance(failure)
            assertFalse(guidance.contains("Wi-Fi"))
            assertFalse(guidance.contains(privateText))
            assertTrue(guidance.contains("kept"))
        }
        assertTrue(commandFailureGuidance(java.security.GeneralSecurityException()).contains("protected storage"))
    }

    @Test fun aFullDiskDoesNotTellTheUserToResetPairing() {
        val disk = loadFailureGuidance(IOException("No space left on device"))
        assertFalse(disk.contains("Reset pairing"))
        assertTrue(disk.contains("Free some storage") && disk.contains("pairing is kept"))
        assertTrue(loadFailureGuidance(IllegalStateException("Device key missing. Reset pairing locally.")).contains("Reset pairing"))
        assertTrue(loadFailureGuidance(javax.crypto.AEADBadTagException()).contains("Reset pairing"))
    }
}
