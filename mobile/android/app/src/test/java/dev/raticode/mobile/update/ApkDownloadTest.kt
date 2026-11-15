package dev.raticode.mobile.update

import java.io.ByteArrayInputStream
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.nio.file.Files
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class ApkDownloadTest {
    private lateinit var folder: File
    private val apk = ByteArray(300_000) { (it * 31 % 251).toByte() }
    private val sha = ApkDownload.sha256(ByteArrayInputStream(apk))
    private val url = "https://github.com/zacharyivie/Raticode/releases/download/mobile-v0.35.0/raticode-mobile-0.35.0.apk"
    private val requests = mutableListOf<Long>()

    @Before fun setUp() { folder = Files.createTempDirectory("raticode-update-test").toFile() }
    @After fun tearDown() { folder.deleteRecursively() }

    private fun target() = File(folder, "raticode-mobile-0.35.0.apk")
    private fun part() = File(target().path + ".part")

    /** A server that honours Range and can drop the connection after [failAfter] bytes. */
    private fun server(body: ByteArray = apk, failAfter: Int = -1, ignoreRange: Boolean = false, finalUrl: String = "https://objects.githubusercontent.com/x") =
        ApkDownload { _, from ->
            requests += from
            val start = if (ignoreRange) 0 else from.toInt()
            val bytes = body.copyOfRange(minOf(start, body.size), body.size)
            val stream: InputStream = if (failAfter < 0) ByteArrayInputStream(bytes) else object : InputStream() {
                var sent = 0
                override fun read(): Int = throw UnsupportedOperationException()
                override fun read(b: ByteArray, off: Int, len: Int): Int {
                    if (sent >= failAfter) throw IOException("connection reset")
                    val n = minOf(len, failAfter - sent, bytes.size - sent)
                    if (n <= 0) return -1
                    System.arraycopy(bytes, sent, b, off, n); sent += n; return n
                }
            }
            if (from > 0 && !ignoreRange) ApkDownload.Response(206, stream, finalUrl, "bytes $from-${body.size - 1}/${body.size}")
            else ApkDownload.Response(200, stream, finalUrl)
        }

    @Test fun aCompleteDownloadIsVerifiedAndKept() {
        val seen = mutableListOf<Long>()
        val file = server().fetch(url, target(), apk.size.toLong(), sha, { false }) { done, _ -> seen += done }
        assertArrayEquals(apk, file.readBytes())
        assertFalse(part().exists())
        assertEquals(apk.size.toLong(), seen.last())
        // Asking again reuses the verified file without a request.
        requests.clear()
        server().fetch(url, target(), apk.size.toLong(), sha, { false }) { _, _ -> }
        assertEquals(emptyList<Long>(), requests)
    }

    @Test fun anInterruptedDownloadResumesWhereItStopped() {
        val failure = assertThrows(UpdateFailure::class.java) { server(failAfter = 100_000).fetch(url, target(), apk.size.toLong(), sha, { false }) { _, _ -> } }
        assertTrue(failure.message!!, failure.message!!.contains("continue where it stopped"))
        assertEquals(100_000L, part().length())
        server().fetch(url, target(), apk.size.toLong(), sha, { false }) { _, _ -> }
        assertEquals(listOf(0L, 100_000L), requests)
        assertArrayEquals(apk, target().readBytes())
    }

    @Test fun cancelKeepsWhatArrivedForNextTime() {
        var reads = 0
        assertThrows(ApkDownload.Cancelled::class.java) { server().fetch(url, target(), apk.size.toLong(), sha, { reads++ > 2 }) { _, _ -> } }
        assertTrue(part().length() in 1 until apk.size)
        server().fetch(url, target(), apk.size.toLong(), sha, { false }) { _, _ -> }
        assertArrayEquals(apk, target().readBytes())
    }

    @Test fun aServerThatIgnoresRangeStartsOver() {
        part().writeBytes(apk.copyOf(50_000))
        server(ignoreRange = true).fetch(url, target(), apk.size.toLong(), sha, { false }) { _, _ -> }
        assertArrayEquals(apk, target().readBytes())
    }

    @Test fun aChecksumMismatchDeletesEverythingAndInstallsNothing() {
        val tampered = apk.copyOf().also { it[1234] = (it[1234] + 1).toByte() }
        val failure = assertThrows(UpdateFailure::class.java) { server(body = tampered).fetch(url, target(), apk.size.toLong(), sha, { false }) { _, _ -> } }
        assertTrue(failure.message!!.contains("didn't match the checksum"))
        assertFalse(target().exists())
        assertFalse(part().exists())
    }

    @Test fun aResumeThatJoinsTwoFilesIsRetriedFromScratchOnce() {
        // A stale partial file from another build: the joined result fails the hash, so one fresh download follows.
        part().writeBytes(ByteArray(100_000) { 7 })
        server().fetch(url, target(), apk.size.toLong(), sha, { false }) { _, _ -> }
        assertEquals(listOf(100_000L, 0L), requests)
        assertArrayEquals(apk, target().readBytes())
    }

    @Test fun oversizedShortAndNonHttpsDownloadsFail() {
        assertThrows(UpdateFailure::class.java) { server(body = apk + ByteArray(10)).fetch(url, target(), apk.size.toLong(), sha, { false }) { _, _ -> } }
        assertFalse(part().exists())
        assertThrows(UpdateFailure::class.java) { server(body = apk.copyOf(1000)).fetch(url, target(), apk.size.toLong(), sha, { false }) { _, _ -> } }
        assertThrows(UpdateFailure::class.java) { server().fetch(url.replace("https", "http"), target(), apk.size.toLong(), sha, { false }) { _, _ -> } }
        assertThrows(UpdateFailure::class.java) { server(finalUrl = "http://evil.example/x").fetch(url, target(), apk.size.toLong(), sha, { false }) { _, _ -> } }
        assertFalse(target().exists())
    }

    @Test fun httpErrorsAndOfflineFailWithWords() {
        val notFound = ApkDownload { _, _ -> ApkDownload.Response(404, ByteArrayInputStream(ByteArray(0)), url) }
        assertTrue(assertThrows(UpdateFailure::class.java) { notFound.fetch(url, target(), apk.size.toLong(), sha, { false }) { _, _ -> } }.message!!.contains("HTTP 404"))
        val offline = ApkDownload { _, _ -> throw java.net.UnknownHostException("github.com") }
        assertTrue(assertThrows(UpdateFailure::class.java) { offline.fetch(url, target(), apk.size.toLong(), sha, { false }) { _, _ -> } }.message!!.contains("stopped"))
    }
}
