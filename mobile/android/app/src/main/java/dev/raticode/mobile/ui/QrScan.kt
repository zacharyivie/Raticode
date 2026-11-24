package dev.raticode.mobile.ui

import android.app.Activity
import android.content.Context
import android.content.Intent
import androidx.activity.result.contract.ActivityResultContract
import com.google.zxing.BarcodeFormat
import com.google.zxing.BinaryBitmap
import com.google.zxing.DecodeHintType
import com.google.zxing.LuminanceSource
import com.google.zxing.PlanarYUVLuminanceSource
import com.google.zxing.ReaderException
import com.google.zxing.common.HybridBinarizer
import com.google.zxing.qrcode.QRCodeReader

/**
 * Opens [SecureCaptureActivity] to scan one QR code. The input is the line shown under the camera; the result is the
 * code's text, or null when the user backs out or Android refuses the camera. Only QR codes are read, the scanner never
 * beeps, and it turns with the phone.
 */
class ScanQrContract : ActivityResultContract<String, String?>() {
    override fun createIntent(context: Context, input: String): Intent =
        Intent(context, SecureCaptureActivity::class.java).putExtra(EXTRA_PROMPT, input)

    override fun parseResult(resultCode: Int, intent: Intent?): String? = result(resultCode, intent?.getStringExtra(EXTRA_TEXT))

    companion object {
        const val EXTRA_PROMPT = "dev.raticode.mobile.scan.PROMPT"
        const val EXTRA_TEXT = "dev.raticode.mobile.scan.TEXT"

        /** A finished scan with text is a result; a cancelled one, or one without text, is not. */
        fun result(resultCode: Int, text: String?): String? = text?.takeIf { resultCode == Activity.RESULT_OK && it.isNotEmpty() }
    }
}

/**
 * Reads a QR code from one camera frame's brightness plane (the Y plane of YUV_420_888). The decoding is ZXing core's;
 * this only lays the plane out for it. A frame without a readable QR code gives null, never an exception, so one bad
 * frame can't end the scan. One instance belongs to one analysis thread.
 */
class QrFrameDecoder {
    private val reader = QRCodeReader()
    private val hints = mapOf(
        DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE),
        DecodeHintType.TRY_HARDER to true,
    )

    /**
     * [plane] holds [height] rows of [rowStride] bytes, with [pixelStride] bytes between pixels. The last row may be cut
     * short at [width] pixels, as camera buffers often are.
     */
    fun decode(plane: ByteArray, width: Int, height: Int, rowStride: Int = width, pixelStride: Int = 1): String? {
        if (width <= 0 || height <= 0 || pixelStride <= 0 || rowStride < (width - 1) * pixelStride + 1) return null
        if (plane.size < rowStride * (height - 1) + (width - 1) * pixelStride + 1) return null
        val packed = if (rowStride == width && pixelStride == 1) plane else ByteArray(width * height).also { out ->
            for (y in 0 until height) {
                val row = y * rowStride
                if (pixelStride == 1) System.arraycopy(plane, row, out, y * width, width)
                else for (x in 0 until width) out[y * width + x] = plane[row + x * pixelStride]
            }
        }
        val source = try {
            PlanarYUVLuminanceSource(packed, width, height, 0, 0, width, height, false)
        } catch (_: IllegalArgumentException) {
            return null
        }
        // Light-on-dark codes, as some dark screens show them, only read inverted.
        return read(source) ?: read(source.invert())
    }

    private fun read(source: LuminanceSource): String? = try {
        reader.decode(BinaryBitmap(HybridBinarizer(source)), hints).text?.takeIf { it.isNotEmpty() }
    } catch (_: ReaderException) {
        null
    } catch (_: RuntimeException) {
        // ZXing can throw unchecked exceptions on malformed frames; the next frame gets its own try.
        null
    } finally {
        reader.reset()
    }
}
