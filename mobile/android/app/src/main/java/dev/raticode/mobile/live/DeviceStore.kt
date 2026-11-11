package dev.raticode.mobile.live

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import dev.raticode.mobile.protocol.StrictJson
import org.json.JSONObject
import java.io.File
import java.math.BigInteger
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Principal
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.security.spec.ECGenParameterSpec
import java.util.Date
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.net.ssl.KeyManager
import javax.net.ssl.X509ExtendedKeyManager
import javax.net.ssl.SSLEngine
import javax.security.auth.x500.X500Principal

/** Independent protected device state; reset explicitly revokes local access to pairing material. */
class DeviceStore(context: Context) {
    private val generation = synchronized(lock) { generations.incrementAndGet() }
    @Volatile private var closed = false
    fun close() = synchronized(lock) { closed = true }
    private fun checkOwner() { check(!closed && generation == generations.get()) { "Device owner replaced" } }
    private val blobs = File(context.noBackupFilesDir, "device-files-v2").apply { mkdirs() }
    private val file = AtomicFile(File(context.noBackupFilesDir, "device-v2.bin"))
    private fun store() = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
    private fun encryptionKey(): SecretKey {
        val ks = store()
        (ks.getKey(DATA, null) as? SecretKey)?.let { return it }
        check(!file.baseFile.exists() && !File(file.baseFile.path + ".bak").exists()) { "Device key missing. Reset pairing locally." }
        return KeyGenerator.getInstance("AES", "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(DATA, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setKeySize(256).setBlockModes("GCM").setEncryptionPaddings("NoPadding").build())
        }.generateKey()
    }
    fun load(): JSONObject = synchronized(lock) {
        if (!file.baseFile.exists() && !File(file.baseFile.path + ".bak").exists()) return JSONObject().put("device_id", UUID.randomUUID().toString())
        require(file.baseFile.length() <= LiveJournal.MAX_JOURNAL_BYTES + 29)
        require(File(file.baseFile.path + ".bak").length() <= LiveJournal.MAX_JOURNAL_BYTES + 29)
        val bytes = file.readFully()
        require(bytes.size <= LiveJournal.MAX_JOURNAL_BYTES + 29)
        require(bytes.size >= 29 && bytes[0] == 2.toByte())
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, encryptionKey(), GCMParameterSpec(128, bytes.copyOfRange(1,13)))
        cipher.updateAAD("device-v2".toByteArray())
        val plain = cipher.doFinal(bytes, 13, bytes.size - 13)
        return try { JSONObject(String(plain, Charsets.UTF_8)) } finally { plain.fill(0) }
    }
    fun save(state: JSONObject) = synchronized(lock) {
        checkOwner()
        val plain = state.toString().toByteArray()
        if (plain.size > LiveJournal.MAX_JOURNAL_BYTES) {
            plain.fill(0)
            throw LiveStorageLimit("Protected live storage is full. Clear completed history and remove finished files. Pending work and replay protection are retained.")
        }
        try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, encryptionKey()); cipher.updateAAD("device-v2".toByteArray())
            val bytes = byteArrayOf(2) + cipher.iv + cipher.doFinal(plain)
            val out = file.startWrite()
            try { out.write(bytes); file.finishWrite(out) } catch (error: Exception) { file.failWrite(out); throw error }
        } finally { plain.fill(0) }
    }
    fun identity(state: JSONObject): Array<KeyManager> = synchronized(lock) {
        checkOwner()
        var ks = store()
        if (!ks.containsAlias(IDENTITY)) {
            check(!state.has("peer") && !state.has("pending_peer")) { "Paired identity missing. Reset and pair again." }
            KeyPairGenerator.getInstance("EC", "AndroidKeyStore").apply {
                initialize(KeyGenParameterSpec.Builder(IDENTITY, KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY)
                    .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1")).setDigests("SHA-256", KeyProperties.DIGEST_NONE)
                    .setCertificateSubject(X500Principal("CN=Raticode Mobile"))
                    .setCertificateSerialNumber(BigInteger(120, SecureRandom()))
                    .setCertificateNotBefore(Date(System.currentTimeMillis() - 86400000))
                    .setCertificateNotAfter(Date(System.currentTimeMillis() + 10L * 365 * 86400000)).build())
            }.generateKeyPair()
            ks = store()
        }
        val key = ks.getKey(IDENTITY, null) as PrivateKey
        val cert = ks.getCertificate(IDENTITY) as X509Certificate
        return arrayOf(object : X509ExtendedKeyManager() {
            override fun getClientAliases(t: String?, i: Array<out Principal>?) = arrayOf(IDENTITY)
            override fun chooseClientAlias(t: Array<out String>?, i: Array<out Principal>?, s: java.net.Socket?) = IDENTITY
            override fun chooseEngineClientAlias(t: Array<out String>?, i: Array<out Principal>?, e: SSLEngine?) = IDENTITY
            override fun getServerAliases(t: String?, i: Array<out Principal>?) = arrayOf(IDENTITY)
            override fun chooseServerAlias(t: String?, i: Array<out Principal>?, s: java.net.Socket?) = IDENTITY
            override fun getCertificateChain(a: String?) = arrayOf(cert)
            override fun getPrivateKey(a: String?) = key
        })
    }
    private fun blob(name: String): AtomicFile {
        require(name.matches(Regex("[a-f0-9-]{36}(-[0-9]+)?")))
        return AtomicFile(File(blobs, name))
    }
    fun writeBlob(name: String, plain: ByteArray) = synchronized(lock) {
        checkOwner(); require(plain.size <= 10 * 1024 * 1024)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, encryptionKey()); cipher.updateAAD(("device-file-v2:" + name).toByteArray())
        val bytes = byteArrayOf(2) + cipher.iv + cipher.doFinal(plain)
        val atomic = blob(name); val out = atomic.startWrite()
        try { out.write(bytes); atomic.finishWrite(out) } catch (error: Exception) { atomic.failWrite(out); throw error }
    }
    fun readBlob(name: String): ByteArray = synchronized(lock) {
        checkOwner(); val atomic = blob(name)
        require(atomic.baseFile.length() <= 10 * 1024 * 1024 + 29)
        val bytes = atomic.readFully(); require(bytes.size >= 29 && bytes[0] == 2.toByte())
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, encryptionKey(), GCMParameterSpec(128, bytes.copyOfRange(1,13)))
        cipher.updateAAD(("device-file-v2:" + name).toByteArray())
        return cipher.doFinal(bytes, 13, bytes.size - 13)
    }
    fun removeTransfer(id: String, chunksOnly: Boolean = false) =
        cleanupTransfers(if (chunksOnly) emptySet() else setOf(id), if (chunksOnly) setOf(id) else emptySet())
    fun cleanupTransfers(removed: Set<String>, completed: Set<String>) = synchronized(lock) {
        checkOwner()
        (removed + completed).forEach { require(java.util.UUID.fromString(it).toString() == it) }
        blobs.listFiles().orEmpty().forEach { candidate ->
            val name = candidate.name.removeSuffix(".bak").removeSuffix(".new")
            if (name.length >= 36) {
                val id = name.take(36)
                val chunk = name.length > 37 && name[36] == '-' && name.substring(37).all { it.isDigit() }
                if ((id in removed && (name == id || chunk)) || (id in completed && chunk))
                    check(candidate.delete() || !candidate.exists()) { "Device file cleanup could not complete" }
            }
        }
    }
    fun removeBlob(name: String) = synchronized(lock) { checkOwner(); blob(name).delete() }
    fun reset() = synchronized(lock) { checkOwner(); file.delete(); blobs.listFiles().orEmpty().forEach { it.delete() }; store().apply { deleteEntry(DATA); deleteEntry(IDENTITY) } }
    companion object { private val lock = Any(); private val generations = java.util.concurrent.atomic.AtomicLong(); private const val DATA = "raticode.device.data.v2"; private const val IDENTITY = "raticode.device.identity.v2" }
}
