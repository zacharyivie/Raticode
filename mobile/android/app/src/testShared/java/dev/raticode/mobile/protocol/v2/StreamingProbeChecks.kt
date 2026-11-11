package dev.raticode.mobile.protocol.v2

import com.google.crypto.tink.InsecureSecretKeyAccess
import com.google.crypto.tink.KeysetHandle
import com.google.crypto.tink.RegistryConfiguration
import com.google.crypto.tink.StreamingAead
import com.google.crypto.tink.TinkJsonProtoKeysetFormat
import com.google.crypto.tink.streamingaead.StreamingAeadConfig
import com.google.crypto.tink.streamingaead.PredefinedStreamingAeadParameters
import org.json.JSONObject
import org.junit.Assert.*
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream

/** Cleartext keyset access is restricted to public fixtures in test source sets. */
class StreamingProbeChecks(private val read: (String) -> ByteArray) {
    init { StreamingAeadConfig.register() }
    private val description = JSONObject(read("streaming.json").toString(Charsets.UTF_8))
    private val aad = description.getString("aad_utf8").toByteArray()
    private val primitive = TinkJsonProtoKeysetFormat.parseKeyset(
        read("streaming-test-only.keyset.json").toString(Charsets.UTF_8), InsecureSecretKeyAccess.get()
    ).getPrimitive(RegistryConfiguration.get(), StreamingAead::class.java)
    private val expected = ByteArray(32768) { it.toByte() }

    fun decryptPythonAndEncrypt(): ByteArray {
        assertArrayEquals(expected, decrypt(read("python-streaming.ciphertext")))
        assertArrayEquals(expected, decrypt(read("java-streaming.ciphertext")))
        val output = ByteArrayOutputStream()
        primitive.newEncryptingStream(output, aad).use { it.write(expected) }
        val encrypted = output.toByteArray()
        assertArrayEquals(expected, decrypt(encrypted))
        return encrypted
    }

    fun rejectsTamperTruncationWrongAad() {
        val ciphertext = read("python-streaming.ciphertext")
        rejected { decrypt(ciphertext.copyOf(ciphertext.size - 1)) }
        rejected { decrypt(ciphertext + byteArrayOf(1)) }
        rejected { decrypt(ciphertext, "wrong AAD".toByteArray()) }
        val wrongKey = KeysetHandle.generateNew(PredefinedStreamingAeadParameters.AES128_GCM_HKDF_1MB)
            .getPrimitive(RegistryConfiguration.get(), StreamingAead::class.java)
        rejected { wrongKey.newDecryptingStream(ByteArrayInputStream(ciphertext), aad).use { it.readBytes() } }
        ciphertext[ciphertext.lastIndex] = (ciphertext.last().toInt() xor 1).toByte()
        rejected { decrypt(ciphertext) }
    }

    fun multiSegmentAndBoundaryTruncation() {
        val plaintext = ByteArray(2 * 1024 * 1024 + 123) { it.toByte() }
        val output = ByteArrayOutputStream()
        primitive.newEncryptingStream(output, aad).use { it.write(plaintext) }
        val ciphertext = output.toByteArray()
        assertArrayEquals(plaintext, decrypt(ciphertext))
        rejected { decrypt(ciphertext.copyOf(1024 * 1024)) }
    }

    private fun decrypt(ciphertext: ByteArray, associatedData: ByteArray = aad): ByteArray =
        primitive.newDecryptingStream(ByteArrayInputStream(ciphertext), associatedData).use { it.readBytes() }

    private fun rejected(block: () -> Unit) {
        var rejected = false
        try { block() } catch (_: Exception) { rejected = true }
        assertTrue("Invalid streaming ciphertext was accepted", rejected)
    }
}
