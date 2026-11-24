package dev.raticode.mobile.ui

import android.app.Activity
import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.MultiFormatWriter
import com.google.zxing.common.BitMatrix
import com.google.zxing.qrcode.QRCodeWriter
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.util.Base64
import java.util.UUID
import kotlin.random.Random

class QrScanTest {
    /** Shaped like the desktop's real invitation, so the code is as dense as the one the phone scans. */
    private val invitation: String = run {
        val token = Base64.getUrlEncoder().withoutPadding().encodeToString(ByteArray(32) { 7 })
        val json = JSONObject().put("version", 2).put("profile", "raticode-tls13-v2").put("invitation_id", UUID.randomUUID())
            .put("responder_id", UUID.randomUUID()).put("responder_name", "Studio desktop").put("responder_role", "desktop")
            .put("responder_spki_sha256", "ab".repeat(32)).put("secret", token).put("expires_at", 1_900_000_000)
            .put("relay", "https://ntfy.sh").put("pair_initiator_topic", token).put("pair_responder_topic", token.reversed())
            .put("lan_endpoint", JSONObject().put("host", "192.168.1.20").put("port", 8767))
        "raticode://pair?v=2&data=" + Base64.getUrlEncoder().withoutPadding().encodeToString(json.toString().toByteArray())
    }

    /** Draws [matrix] dark-on-light into a camera-sized brightness plane, [scale] pixels per module, offset from the corner. */
    private fun frame(matrix: BitMatrix, width: Int, height: Int, rowStride: Int = width, scale: Int = 3,
                      left: Int = 40, top: Int = 30, dark: Byte = 20, light: Byte = 230.toByte()): ByteArray {
        val plane = ByteArray(rowStride * height) { light }
        for (y in 0 until matrix.height) for (x in 0 until matrix.width) if (matrix[x, y]) {
            for (dy in 0 until scale) for (dx in 0 until scale) plane[(top + y * scale + dy) * rowStride + left + x * scale + dx] = dark
        }
        // Row padding past the image holds whatever the camera left there.
        for (y in 0 until height) for (x in width until rowStride) plane[y * rowStride + x] = Random(y).nextInt().toByte()
        return plane
    }

    private fun qr(text: String = invitation) = QRCodeWriter().encode(text, BarcodeFormat.QR_CODE, 0, 0, mapOf(EncodeHintType.MARGIN to 4))

    private fun rotated(m: BitMatrix) = BitMatrix(m.height, m.width).also { out ->
        for (y in 0 until m.height) for (x in 0 until m.width) if (m[x, y]) out.set(m.height - 1 - y, x)
    }

    @Test fun readsTheInvitationFromAFrame() {
        assertEquals(invitation, QrFrameDecoder().decode(frame(qr(), 640, 480), 640, 480))
    }

    @Test fun skipsRowPaddingAndAShortLastRow() {
        val padded = frame(qr(), 640, 480, rowStride = 704)
        assertEquals(invitation, QrFrameDecoder().decode(padded, 640, 480, rowStride = 704))
        // Camera buffers often stop at the last pixel instead of the end of the last row's padding.
        val short = padded.copyOf(704 * 479 + 640)
        assertEquals(invitation, QrFrameDecoder().decode(short, 640, 480, rowStride = 704))
    }

    @Test fun readsInterleavedPixels() {
        val packed = frame(qr(), 640, 480)
        val spaced = ByteArray(packed.size * 2).also { for (i in packed.indices) it[i * 2] = packed[i] }
        assertEquals(invitation, QrFrameDecoder().decode(spaced, 640, 480, rowStride = 1280, pixelStride = 2))
    }

    @Test fun readsACodeTurnedWithThePhone() {
        assertEquals(invitation, QrFrameDecoder().decode(frame(rotated(qr()), 480, 640), 480, 640))
    }

    @Test fun readsALightOnDarkCode() {
        val inverted = frame(qr(), 640, 480, dark = 230.toByte(), light = 20)
        assertEquals(invitation, QrFrameDecoder().decode(inverted, 640, 480))
    }

    @Test fun keepsReadingAfterFramesWithoutACode() {
        val decoder = QrFrameDecoder()
        val noise = ByteArray(640 * 480).also { Random(1).nextBytes(it) }
        assertNull(decoder.decode(noise, 640, 480))
        assertNull(decoder.decode(ByteArray(640 * 480) { 128.toByte() }, 640, 480))
        assertEquals(invitation, decoder.decode(frame(qr(), 640, 480), 640, 480))
    }

    @Test fun ignoresOtherBarcodes() {
        val barcode = MultiFormatWriter().encode("raticode://pair?v=2&data=abc", BarcodeFormat.CODE_128, 500, 80)
        val plane = ByteArray(640 * 480) { 230.toByte() }
        for (y in 0 until 80) for (x in 0 until 500) if (barcode[x, y]) plane[(200 + y) * 640 + 70 + x] = 20
        assertNull(QrFrameDecoder().decode(plane, 640, 480))
        val dataMatrix = MultiFormatWriter().encode("raticode://pair?v=2&data=abc", BarcodeFormat.DATA_MATRIX, 0, 0)
        assertNull(QrFrameDecoder().decode(frame(dataMatrix, 640, 480, scale = 8), 640, 480))
    }

    @Test fun refusesFramesThatDontMatchTheirSize() {
        val decoder = QrFrameDecoder()
        assertNull(decoder.decode(ByteArray(10), 640, 480))
        assertNull(decoder.decode(ByteArray(0), 0, 0))
        assertNull(decoder.decode(ByteArray(640 * 480), 640, 480, rowStride = 600))
        assertNull(decoder.decode(ByteArray(640 * 480), 640, 480, pixelStride = 0))
    }

    @Test fun onlyAFinishedScanWithTextIsAResult() {
        assertEquals("raticode://pair?v=2&data=x", ScanQrContract.result(Activity.RESULT_OK, "raticode://pair?v=2&data=x"))
        assertNull(ScanQrContract.result(Activity.RESULT_CANCELED, "raticode://pair?v=2&data=x"))
        assertNull(ScanQrContract.result(Activity.RESULT_OK, null))
        assertNull(ScanQrContract.result(Activity.RESULT_OK, ""))
    }
}
