package dev.raticode.mobile.protocol.v2

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class StreamingDeviceTest {
    private val checks = StreamingProbeChecks { name ->
        InstrumentationRegistry.getInstrumentation().context.assets.open(name).use { it.readBytes() }
    }
    @Test fun pythonToAndroid() { checks.decryptPythonAndEncrypt() }
    @Test fun tamperTruncationWrongAad() = checks.rejectsTamperTruncationWrongAad()
    @Test fun multiSegmentBoundaries() = checks.multiSegmentAndBoundaryTruncation()
}
