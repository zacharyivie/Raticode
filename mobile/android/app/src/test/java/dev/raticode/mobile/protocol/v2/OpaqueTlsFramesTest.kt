package dev.raticode.mobile.protocol.v2

import org.junit.Assert.*
import org.junit.Test
import java.nio.ByteBuffer

class OpaqueTlsFramesTest {
    private val session = "00000000-0000-4000-8000-000000000001"
    private fun frame(index: Long, body: ByteArray = byteArrayOf(1)) =
        OpaqueTlsFrame(session, "resume", "initiator_to_responder", index, body).encode()
    private fun stream() = OpaqueTlsReorder(session, "resume", "initiator_to_responder")
    private fun rejected(block: () -> Unit) {
        var rejected = false
        try { block() } catch (_: Exception) { rejected = true }
        assertTrue(rejected)
    }
    @Test fun exactReorderBudgetAndBoundary() {
        val receiver = stream()
        for (index in 1L..32L) assertTrue(receiver.accept(frame(index, ByteArray(2048)), 0).isEmpty())
        val recovered = receiver.accept(frame(0), 29999)
        assertEquals(33, recovered.size)
        assertTrue(receiver.accept(frame(32, ByteArray(2048)), 29999).isEmpty())
        rejected { receiver.accept(frame(32, byteArrayOf(3)), 29999) }
        rejected { receiver.accept(frame(33), 29999) }
        val tooFar = stream()
        rejected { tooFar.accept(frame(33), 0) }
    }
    @Test fun gapExpiresWithoutTrafficAndDuplicateDoesNotExtend() {
        val receiver = stream()
        receiver.accept(frame(1), 100)
        receiver.accept(frame(1), 30099)
        rejected { receiver.expire(30100) }
        val wrongSession = stream()
        rejected { wrongSession.accept(frame(0).toString(Charsets.UTF_8).replace(session, "00000000-0000-4000-8000-000000000002").toByteArray(), 0) }
    }
    @Test fun strictFrameByteBoundsAndCanonicalEncoding() {
        assertEquals(2048, OpaqueTlsFrame.parse(frame(0, ByteArray(2048))).body.size)
        rejected { frame(0, ByteArray(2049)) }
        rejected { OpaqueTlsFrame.parse(ByteArray(3073)) }
        val padded = frame(0).toString(Charsets.UTF_8).replace("AQ", "AQ==").toByteArray()
        rejected { OpaqueTlsFrame.parse(padded) }
        rejected { OpaqueTlsFrame.parse(frame(0).toString(Charsets.UTF_8).replace("\"version\":2", "\"version\":2,\"version\":2").toByteArray()) }
    }
    @Test fun recordAcrossEveryByteAndTruncation() {
        val json = "{\"type\":\"probe\",\"text\":\"Rem\"}".toByteArray()
        val framed = TlsJsonRecords.encode(json)
        val decoder = TlsJsonRecords()
        val records = framed.flatMap { decoder.feed(byteArrayOf(it)) }
        assertEquals(1, records.size)
        assertEquals("Rem", records.single().getString("text"))
        decoder.endOfStream()
        val truncated = TlsJsonRecords()
        truncated.feed(framed.copyOf(framed.size - 1))
        rejected { truncated.endOfStream() }
        rejected { truncated.feed(byteArrayOf(framed.last())) }
        rejected { TlsJsonRecords().feed(ByteBuffer.allocate(4).putInt(65537).array()) }
        rejected { TlsJsonRecords().feed(ByteBuffer.allocate(4).putInt(-1).array()) }
        rejected { TlsJsonRecords().feed(ByteArray(4)) }
    }
    @Test fun maximumRecordAndDuplicateMembers() {
        val maximum = ("{\"v\":\"" + "a".repeat(65528) + "\"}").toByteArray()
        assertEquals(65536, maximum.size)
        val framed = TlsJsonRecords.encode(maximum)
        val decoder = TlsJsonRecords()
        assertTrue(decoder.feed(framed.copyOfRange(0, 4)).isEmpty())
        assertEquals(1, decoder.feed(framed.copyOfRange(4, framed.size)).size)
        rejected { TlsJsonRecords.encode("{\"v\":1,\"v\":2}".toByteArray()) }
    }
    @Test fun collectionBoundaries() {
        fun record(size: Int, array: Boolean): ByteArray = if (array)
            ("{\"v\":[" + List(size) { "0" }.joinToString(",") + "]}").toByteArray()
        else ("{" + List(size) { "\"v$it\":0" }.joinToString(",") + "}").toByteArray()
        for (array in listOf(true, false)) {
            TlsJsonRecords.encode(record(1024, array))
            rejected { TlsJsonRecords.encode(record(1025, array)) }
        }
    }
}
