package dev.raticode.mobile.browser

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import java.io.File
import java.security.KeyStore
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicReference
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * The phone browser's tabs, history, bookmarks and choices, encrypted with a key held by Android Keystore in no-backup
 * storage, like the People list. Nothing here goes to the desktop or the log. Pages' own cookies, storage and cache live
 * in Android WebView's private app data, which Android encrypts with the phone's storage; **Clear browsing data**
 * removes them. Writes run on one background thread and coalesce, so a burst of page loads saves once.
 */
internal object BrowserStore {
    private val file = EncryptedJsonFile("raticode.browser.v1", "browser-v1", "browser-v1.bin")

    fun load(context: Context): BrowserRules.Saved = file.load(context)?.let { BrowserRules.decode(it) } ?: BrowserRules.Saved()

    fun save(context: Context, saved: BrowserRules.Saved) = file.save(context, BrowserRules.encode(saved))
}

/**
 * One JSON document, encrypted with a key held by Android Keystore, in no-backup storage. A damaged file or a lost key
 * (a restored or reset phone) reads as empty rather than guessing. Writes run on one background thread and coalesce.
 */
internal class EncryptedJsonFile(private val keyAlias: String, private val aad: String, private val fileName: String) {
    private val writer = Executors.newSingleThreadExecutor { Thread(it, "browser-store").apply { isDaemon = true } }
    private val waiting = AtomicReference<String?>(null)

    private fun file(context: Context) = AtomicFile(File(context.applicationContext.noBackupFilesDir, fileName))

    /** The document's text, or null when there is none or it can't be read. */
    fun load(context: Context): String? = runCatching {
        val atomic = file(context)
        if (!atomic.baseFile.exists() && !File(atomic.baseFile.path + ".bak").exists()) return null
        val bytes = atomic.readFully()
        require(bytes.size in 29..MAX_BYTES && bytes[0] == 1.toByte())
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, secret(), GCMParameterSpec(128, bytes.copyOfRange(1, 13)))
        cipher.updateAAD(aad.toByteArray())
        val plain = cipher.doFinal(bytes, 13, bytes.size - 13)
        try { String(plain, Charsets.UTF_8) } finally { plain.fill(0) }
    }.getOrElse {
        file(context).delete(); null
    }

    fun save(context: Context, text: String) {
        val app = context.applicationContext
        if (waiting.getAndSet(text) != null) return
        writer.execute {
            val latest = waiting.getAndSet(null) ?: return@execute
            runCatching { write(file(app), latest) }
        }
    }

    private fun write(atomic: AtomicFile, text: String) {
        val plain = text.toByteArray()
        try {
            if (plain.size > MAX_BYTES - 29) return
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, secret()); cipher.updateAAD(aad.toByteArray())
            val bytes = byteArrayOf(1) + cipher.iv + cipher.doFinal(plain)
            val out = atomic.startWrite()
            try { out.write(bytes); atomic.finishWrite(out) } catch (error: Exception) { atomic.failWrite(out); throw error }
        } finally { plain.fill(0) }
    }

    private fun secret(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(keyAlias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance("AES", "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(keyAlias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setKeySize(256).setBlockModes("GCM").setEncryptionPaddings("NoPadding").build())
        }.generateKey()
    }

    private companion object { const val MAX_BYTES = 4 * 1024 * 1024 }
}
