package dev.raticode.mobile.data

import android.content.Context
import android.net.Uri
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import java.io.File
import java.security.KeyStore
import java.security.MessageDigest
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.CancellationException
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Android Keystore AES-GCM at rest. This is not the desktop E2EE protocol. Call on IO. */
class LocalVault(private val context: Context, private val resolver: android.content.ContentResolver = context.contentResolver) {
    private val directory = File(context.noBackupFilesDir, "workspace").apply { mkdirs() }
    private val snapshot = File(directory, "state.bin")
    // All instances use the same lock, including direct vault readers and reset callers.
    // A cancelled synchronous write finishes before a replacement owner can load or reset.
    internal var snapshotWriteStarted: (() -> Unit)? = null
    internal var ownedOperationQueued: (() -> Unit)? = null
    fun acquireOwner(): Owner = Owner(ownerGeneration.incrementAndGet()) { ownedOperationQueued?.invoke() }
    class Owner internal constructor(private val generation: Long, private val beforeLock: () -> Unit) {
        private val closed = AtomicBoolean(false)
        fun close() { closed.set(true) }
        fun <T> run(operation: () -> T): T {
            beforeLock()
            return synchronized(operationLock) {
                if (closed.get() || generation != ownerGeneration.get()) {
                    throw CancellationException("Workspace owner was replaced")
                }
                operation()
            }
        }
    }
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        // Never replace a lost key while encrypted user data still exists.
        check(directory.listFiles().orEmpty().none { it.extension == "bin" }) { "Local encryption key is unavailable" }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256).setRandomizedEncryptionRequired(true).build())
        }.generateKey()
    }
    private fun encrypt(bytes: ByteArray, purpose: String): ByteArray {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        cipher.updateAAD(purpose.toByteArray(Charsets.UTF_8))
        return byteArrayOf(1) + cipher.iv + cipher.doFinal(bytes)
    }
    private fun decrypt(bytes: ByteArray, purpose: String): ByteArray {
        require(bytes.size >= 29 && bytes[0] == 1.toByte())
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes.copyOfRange(1, 13)))
        cipher.updateAAD(purpose.toByteArray(Charsets.UTF_8))
        return cipher.doFinal(bytes, 13, bytes.size - 13)
    }
    private fun atomicWrite(file: File, bytes: ByteArray) {
        val atomic = AtomicFile(file)
        val output = atomic.startWrite()
        try { if (file == snapshot) snapshotWriteStarted?.invoke(); output.write(bytes); atomic.finishWrite(output) } catch (e: Exception) { atomic.failWrite(output); throw e }
    }
    fun load(): Workspace = synchronized(operationLock) {
        val atomic = AtomicFile(snapshot)
        if (!snapshot.exists() && !File(directory, "state.bin.bak").exists()) return Workspace()
        require(snapshot.length() <= 64L * 1024 * 1024)
        return WorkspaceCodec.decode(decrypt(atomic.readFully(), "workspace-v1"))
    }
    fun save(state: Workspace) = synchronized(operationLock) {
        val bytes = WorkspaceCodec.encode(state)
        require(bytes.size <= 48 * 1024 * 1024) { "The local workspace is full. Delete an older conversation." }
        try { atomicWrite(snapshot, encrypt(bytes, "workspace-v1")) } finally { bytes.fill(0) }
    }
    fun cleanup(state: Workspace) = synchronized(operationLock) {
        val keep = state.files().map { "${it.id}.bin" }.toSet() + "state.bin"
        directory.listFiles().orEmpty().filter { it.name !in keep }.forEach { it.delete() }
    }
    fun reset() = synchronized(operationLock) {
        check(directory.listFiles().orEmpty().all { it.delete() }) { "Could not remove local data" }
        KeyStore.getInstance("AndroidKeyStore").apply { load(null); deleteEntry(KEY_ALIAS) }
    }
    fun storeImport(prepared: DocumentImport.Prepared, state: Workspace): LocalFile = synchronized(operationLock) {
        val bytes = prepared.bytes
        try {
            require(bytes.size <= MAX_FILE) { "Choose a file smaller than 10 MiB." }
            require(state.files().sumOf { it.size } + bytes.size <= MAX_TOTAL) { "Local attachments are limited to 50 MiB. Delete a conversation to free space." }
            val file = LocalFile(newId(), prepared.name, "application/octet-stream", bytes.size.toLong(), digest(bytes))
            atomicWrite(blob(file.id), encrypt(bytes, "attachment:${file.id}"))
            return file
        } finally { prepared.close() }
    }
    /** Snapshot is authenticated while vault ownership is held. Provider IO must run outside it. */
    fun prepareExport(file: LocalFile): ByteArray = synchronized(operationLock) {
        require(file.size in 0..MAX_FILE)
        val source = blob(file.id)
        require(source.length() <= MAX_FILE + 29)
        val bytes = decrypt(source.readBytes(), "attachment:${file.id}")
        if (bytes.size.toLong() != file.size || digest(bytes) != file.sha256) {
            bytes.fill(0)
            error("File integrity check failed.")
        }
        bytes
    }
    fun writeExport(bytes: ByteArray, destination: Uri) {
        try {
            requireNotNull(resolver.openOutputStream(destination, "wt")) { "Cannot save at this location." }.use { it.write(bytes) }
        } finally { bytes.fill(0) }
    }
    fun exportFile(file: LocalFile, destination: Uri) = writeExport(prepareExport(file), destination)
    private fun blob(id: String): File {
        require(java.util.UUID.fromString(id).toString() == id)
        return File(directory, "$id.bin")
    }
    companion object {
        private val operationLock = Any()
        private val ownerGeneration = AtomicLong()
        private const val KEY_ALIAS = "raticode.local.v1"
        const val MAX_FILE = 10L * 1024 * 1024
        const val MAX_TOTAL = 50L * 1024 * 1024
        fun safeName(value: String): String = value.substringAfterLast('/').substringAfterLast('\\')
            .filterNot { it.isISOControl() || it in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069" }.trim().take(160).takeUnless { it.isEmpty() || it == "." || it == ".." } ?: "attachment"
        fun digest(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
    }
}
