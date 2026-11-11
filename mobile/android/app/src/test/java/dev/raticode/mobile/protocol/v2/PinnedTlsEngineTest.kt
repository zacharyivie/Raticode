package dev.raticode.mobile.protocol.v2

import org.junit.Test

class PinnedTlsEngineTest {
    private val probe = TlsProbeChecks { name -> javaClass.classLoader!!.getResourceAsStream(name)!!.use { it.readBytes() } }
    @Test fun fragmentedMutualTlsRoundTrip() = probe.roundTrip()
    @Test fun wrongPinsFailBothDirections() = probe.wrongPin()
    @Test fun tamperReplayAndPreviousSessionFail() = probe.tamperAndReplay()
    @Test fun boundsAndHandshakeRequired() = probe.boundsAndPrematureData()
    @Test fun invalidCertificateProfiles() = probe.invalidCertificateProfiles()
    @Test fun maximumRecordsThroughTls() = probe.maximumRecordsThroughTls()
}
