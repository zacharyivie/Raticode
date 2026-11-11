package dev.raticode.mobile.live

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.ContactsContract.CommonDataKinds.Phone
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import androidx.activity.result.contract.ActivityResultContract
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * People the user picked for Rem's call and text cards, kept on this phone only. The list is encrypted with a key held by
 * Android Keystore and lives in no-backup storage. Nothing here is sent to the desktop or written to the log; Rem only
 * ever learns the words the user said. Resetting pairing doesn't remove it; the People page and clearing the app's
 * data do.
 */
object PeopleStore {
    private const val KEY = "raticode.people.v1"
    private const val AAD = "people-v1"
    private const val MAX_BYTES = 256 * 1024
    private var file: AtomicFile? = null

    /** The saved people, newest first. Compose screens redraw when it changes. */
    var people by mutableStateOf<List<SavedPerson>>(emptyList()); private set
    /**
     * The person picked for a particular card, for as long as the app runs, so the card shows them when voice mode or the
     * chat comes back from the picker. Keyed by the card itself: the same words and message on two cards share it.
     */
    val chosen = mutableStateMapOf<PhoneAction, SavedPerson>()
    /** Why the last pick for a card didn't give a usable number, such as the user backing out of the picker. */
    val notes = mutableStateMapOf<PhoneAction, String>()

    @Synchronized fun load(context: Context) {
        if (file != null) return
        val atomic = AtomicFile(File(context.applicationContext.noBackupFilesDir, "people-v1.bin"))
        file = atomic
        people = runCatching { read(atomic) }.getOrElse {
            // The key is gone (a restored or reset phone) or the file is damaged: start again rather than guess.
            atomic.delete(); emptyList()
        }
    }

    fun update(context: Context, change: (List<SavedPerson>) -> List<SavedPerson>) {
        load(context)
        val next = change(people)
        write(requireNotNull(file), next)
        people = next
    }

    /**
     * Records what the picker returned for [card]: the card then opens that person, and with [remember] they answer to
     * the card's words from now on. Returns a sentence when the number can't be used.
     */
    fun picked(context: Context, card: PhoneAction, contact: PickedContact?, remember: Boolean): String? {
        notes.remove(card)
        val problem = when {
            contact == null -> NOT_CHOSEN
            PhonePeople.usableNumber(contact.number) == null -> UNUSABLE
            else -> null
        }
        if (problem != null) { notes[card] = problem; return problem }
        val now = System.currentTimeMillis()
        if (remember) update(context) { PhonePeople.remember(it, contact!!, PhoneActions.recipientOf(card), now) }
        chosen[card] = PhonePeople.once(contact!!, now)
        return null
    }

    private fun secret(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(KEY, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance("AES", "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(KEY, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setKeySize(256).setBlockModes("GCM").setEncryptionPaddings("NoPadding").build())
        }.generateKey()
    }

    private fun read(atomic: AtomicFile): List<SavedPerson> {
        if (!atomic.baseFile.exists() && !File(atomic.baseFile.path + ".bak").exists()) return emptyList()
        val bytes = atomic.readFully()
        require(bytes.size in 29..MAX_BYTES && bytes[0] == 1.toByte())
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, secret(), GCMParameterSpec(128, bytes.copyOfRange(1, 13)))
        cipher.updateAAD(AAD.toByteArray())
        val plain = cipher.doFinal(bytes, 13, bytes.size - 13)
        return try { PhonePeople.decode(String(plain, Charsets.UTF_8)) } finally { plain.fill(0) }
    }

    private fun write(atomic: AtomicFile, people: List<SavedPerson>) {
        if (people.isEmpty()) { atomic.delete(); return }
        val plain = PhonePeople.encode(people).toByteArray()
        try {
            require(plain.size <= MAX_BYTES - 29)
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, secret()); cipher.updateAAD(AAD.toByteArray())
            val bytes = byteArrayOf(1) + cipher.iv + cipher.doFinal(plain)
            val out = atomic.startWrite()
            try { out.write(bytes); atomic.finishWrite(out) } catch (error: Exception) { atomic.failWrite(out); throw error }
        } finally { plain.fill(0) }
    }

    const val NOT_CHOSEN = "No contact chosen."
    const val UNUSABLE = "That number has codes, pauses or letters, so Raticode won't open it. Choose another number or dial it yourself."
}

/**
 * Android's contact picker, asking for one phone number. The user picks a person and the exact number in the phone's
 * contacts app; Raticode gets read access to that one entry only, so it needs no contacts permission.
 */
class PickPhoneNumber : ActivityResultContract<Unit, Uri?>() {
    override fun createIntent(context: Context, input: Unit): Intent = Intent(Intent.ACTION_PICK).setType(Phone.CONTENT_TYPE)
    override fun parseResult(resultCode: Int, intent: Intent?): Uri? = intent?.data.takeIf { resultCode == Activity.RESULT_OK }
}

/** Reads the picked entry through the picker's one-time grant. Null when it can't be read. Call off the main thread. */
fun readPickedContact(context: Context, uri: Uri): PickedContact? = try {
    val cursor = context.contentResolver.query(uri, arrayOf(Phone.NUMBER, Phone.TYPE, Phone.LABEL, Phone.DISPLAY_NAME, Phone.LOOKUP_KEY),
        null, null, null)
    cursor?.use {
        if (!it.moveToFirst() || it.isNull(0)) null
        else PickedContact(it.getString(3).orEmpty().ifBlank { "Contact" }, it.getString(0),
            Phone.getTypeLabel(context.resources, if (it.isNull(1)) Phone.TYPE_OTHER else it.getInt(1), it.getString(2)).toString(),
            it.getString(4).orEmpty())
    }
} catch (_: SecurityException) { null } catch (_: IllegalArgumentException) { null }
