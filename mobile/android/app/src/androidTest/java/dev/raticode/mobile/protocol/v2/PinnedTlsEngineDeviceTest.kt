package dev.raticode.mobile.protocol.v2

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class PinnedTlsEngineDeviceTest {
    private val probe = TlsProbeChecks { name ->
        InstrumentationRegistry.getInstrumentation().context.assets.open(name).use { it.readBytes() }
    }
    @Test fun fragmentedMutualTlsRoundTrip() = probe.roundTrip()
    @Test fun wrongPinsFailBothDirections() = probe.wrongPin()
    @Test fun tamperReplayAndPreviousSessionFail() = probe.tamperAndReplay()
    @Test fun boundsAndHandshakeRequired() = probe.boundsAndPrematureData()
    @Test fun invalidCertificateProfiles() = probe.invalidCertificateProfiles()
    @Test fun maximumRecordsThroughTls() = probe.maximumRecordsThroughTls()
}
