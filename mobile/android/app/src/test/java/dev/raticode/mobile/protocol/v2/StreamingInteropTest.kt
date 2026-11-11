package dev.raticode.mobile.protocol.v2

import org.junit.Test
import java.io.File

class StreamingInteropTest {
    private val checks = StreamingProbeChecks { name -> javaClass.classLoader!!.getResourceAsStream(name)!!.use { it.readBytes() } }
    @Test fun pythonToJavaAndJavaCiphertext() {
        val bytes = checks.decryptPythonAndEncrypt()
        File("build/protocol-probe/java-streaming.ciphertext").apply { parentFile!!.mkdirs(); writeBytes(bytes) }
    }
    @Test fun tamperTruncationWrongAad() = checks.rejectsTamperTruncationWrongAad()
    @Test fun multiSegmentBoundaries() = checks.multiSegmentAndBoundaryTruncation()
}
