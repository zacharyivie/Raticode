package dev.raticode.mobile.share

import android.app.Activity
import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.OpenableColumns
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.IntentCompat
import androidx.core.content.edit
import androidx.core.content.pm.ShortcutInfoCompat
import androidx.core.content.pm.ShortcutManagerCompat
import androidx.core.graphics.drawable.IconCompat
import dev.raticode.mobile.MainActivity
import dev.raticode.mobile.R
import dev.raticode.mobile.applock.AppLockRuntime
import dev.raticode.mobile.childlock.NotificationOpenActivity
import dev.raticode.mobile.childlock.closedByChildLock
import dev.raticode.mobile.live.LiveNotifications
import dev.raticode.mobile.live.RemAlertRules

/**
 * Run a workflow, the second entry in Android's share sheet. It has no window: it keeps the shared text, or the address
 * of one shared file, in memory only, opens More › Workflows to pick a workflow and the input it goes in, and closes.
 * The main screen gets Android's read grant for the file along with the request to open, and reads it only when the
 * user taps Run: it uploads the file the way Share to Rem does, then sends the same run request, through the same
 * suspicious-request check, as a run typed on the phone.
 */
class WorkflowShareActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (closedByChildLock()) return
        // The manifest turns this screen off below Android 10, which can't hold the live connection.
        if (Build.VERSION.SDK_INT < 29) { finish(); return }
        val send = intent.action == Intent.ACTION_SEND || intent.action == Intent.ACTION_SEND_MULTIPLE
        val uris = if (!send) emptyList() else (when (intent.action) {
            Intent.ACTION_SEND -> listOfNotNull(IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, Uri::class.java))
            else -> IntentCompat.getParcelableArrayListExtra(intent, Intent.EXTRA_STREAM, Uri::class.java).orEmpty().filterNotNull()
        }.ifEmpty { (0 until (intent.clipData?.itemCount ?: 0)).mapNotNull { intent.clipData?.getItemAt(it)?.uri } }).distinct()
        // Only content from another app counts; Raticode's own files and plain file paths are left out, as Share to Rem does.
        val readable = uris.filter { uri ->
            WorkflowShare.readable(uri.scheme, uri.authority, packageName) { packageManager.resolveContentProvider(it, 0)?.packageName }
        }
        val files = if (readable.size == 1) listOfNotNull(describe(readable.single(), intent.type)) else readable.map { WorkflowShare.SharedFile(it.toString(), "", null, "") }
        WorkflowShareInbox.receive(
            subject = if (send) intent.getCharSequenceExtra(Intent.EXTRA_SUBJECT) else null,
            text = if (send) intent.getCharSequenceExtra(Intent.EXTRA_TEXT) else null,
            hasFiles = uris.isNotEmpty(),
            shortcutId = if (send) intent.getStringExtra(Intent.EXTRA_SHORTCUT_ID) else null,
            files = files,
        )
        val open = Intent(this, MainActivity::class.java).putExtra(LiveNotifications.THREAD_EXTRA, "live:" + WorkflowShare.TARGET)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or LiveNotifications.OPEN_FLAGS)
        // The read grant for the one shared file goes with the request to open, so Run can read it later.
        files.singleOrNull()?.let { file ->
            open.clipData = ClipData.newRawUri("", Uri.parse(file.uri))
            open.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        // Starting the main screen would clear an app lock already showing. Its unlock opens this instead.
        if (!AppLockRuntime.holdUntilUnlocked(this, open)) startActivity(open)
        finish()
    }

    /** The shared file's name, size and type as the sharing app reports them; nothing of its content is read. */
    private fun describe(uri: Uri, intentType: String?): WorkflowShare.SharedFile? = runCatching {
        var name: String? = null
        var size: Long? = null
        contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) {
                name = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME).takeIf { it >= 0 }?.let(cursor::getString)
                size = cursor.getColumnIndex(OpenableColumns.SIZE).takeIf { it >= 0 && !cursor.isNull(it) }?.let(cursor::getLong)
            }
        }
        val type = contentResolver.getType(uri) ?: intentType?.takeUnless { it.contains('*') }.orEmpty()
        WorkflowShare.SharedFile(uri.toString(), (name ?: uri.lastPathSegment).orEmpty().substringAfterLast("/").filter { it >= ' ' }.trim().take(160).ifBlank { "shared file" }, size, type)
    }.getOrNull()
}

/**
 * The share waiting for a workflow. It lives in memory only, so it is gone if Android ends Raticode, and only
 * [WorkflowShareActivity] puts one here: an app that opens Raticode's main screen directly can't fill in a run.
 */
object WorkflowShareInbox {
    var pending by mutableStateOf<WorkflowShare.Pending?>(null); private set
    private var next = 0L

    fun receive(subject: CharSequence?, text: CharSequence?, hasFiles: Boolean, shortcutId: String?,
                files: List<WorkflowShare.SharedFile> = emptyList()) {
        pending = WorkflowShare.pending(++next, subject, text, hasFiles, shortcutId, files)
    }

    /** The workflow the user chose for the waiting share, or null to choose again. */
    fun target(workflowId: String?) { pending = pending?.copy(target = workflowId, workflowId = null) }

    fun clear() { pending = null }
}

/**
 * What the phone remembers for Run a workflow: the input chosen per workflow, and the three workflows most recently run
 * from a share, which Android offers as direct-share shortcuts. Nothing shared is kept.
 */
object WorkflowShareStore {
    private const val INPUTS = "inputs"
    private const val RECENT = "recent"
    private fun prefs(context: Context) = context.getSharedPreferences("workflow_share", Context.MODE_PRIVATE)

    fun input(context: Context, workflowId: String): String? =
        WorkflowShare.parseInputs(prefs(context).getString(INPUTS, null)).firstOrNull { it.first == workflowId }?.second

    fun rememberInput(context: Context, workflowId: String, input: String) {
        val prefs = prefs(context)
        val next = WorkflowShare.remember(WorkflowShare.parseInputs(prefs.getString(INPUTS, null)), workflowId, input)
        prefs.edit { putString(INPUTS, WorkflowShare.encodeInputs(next)) }
    }

    /** A run started from a share: the workflow moves to the front of the direct-share shortcuts. */
    fun used(context: Context, workflowId: String, name: String) {
        val prefs = prefs(context)
        val before = WorkflowShare.parseRecent(prefs.getString(RECENT, null))
        val after = WorkflowShare.used(before, WorkflowShare.Recent(workflowId, name))
        prefs.edit { putString(RECENT, WorkflowShare.encodeRecent(after)) }
        if (Build.VERSION.SDK_INT >= 29) publish(context, before, after)
    }

    /** Unpairing forgets everything, including the shortcuts, which name the old desktop's workflows. */
    fun clear(context: Context) {
        val before = WorkflowShare.parseRecent(prefs(context).getString(RECENT, null))
        prefs(context).edit { clear() }
        if (Build.VERSION.SDK_INT >= 29) runCatching { remove(context, before.map { WorkflowShare.shortcutId(it.id) }) }
    }

    private fun publish(context: Context, before: List<WorkflowShare.Recent>, after: List<WorkflowShare.Recent>) {
        runCatching {
            // Only this feature's shortcuts are touched; Rem's conversation shortcut stays.
            remove(context, before.filter { old -> after.none { it.id == old.id } }.map { WorkflowShare.shortcutId(it.id) })
            after.forEachIndexed { rank, recent ->
                ShortcutManagerCompat.pushDynamicShortcut(context, ShortcutInfoCompat.Builder(context, WorkflowShare.shortcutId(recent.id))
                    .setShortLabel(WorkflowShare.shortLabel(recent.name)).setLongLabel(WorkflowShare.longLabel(recent.name))
                    .setIcon(IconCompat.createWithResource(context, R.drawable.ic_workflow_shortcut))
                    .setCategories(setOf(WorkflowShare.CATEGORY)).setLongLived(true).setRank(rank)
                    // Shared to, the share target receives the text. Tapped in the launcher's menu, it opens the workflow.
                    // It isn't excluded from the launcher: below Android 13 that would keep it out of the share sheet too.
                    .setIntent(NotificationOpenActivity.intent(context).setAction(Intent.ACTION_VIEW)
                        .putExtra(LiveNotifications.THREAD_EXTRA, "live:" + RemAlertRules.workflowTarget(recent.id, "")))
                    .build())
            }
            ShortcutManagerCompat.reportShortcutUsed(context, WorkflowShare.shortcutId(after.first().id))
        }
    }

    private fun remove(context: Context, ids: List<String>) {
        if (ids.isEmpty()) return
        ShortcutManagerCompat.removeDynamicShortcuts(context, ids)
        ShortcutManagerCompat.removeLongLivedShortcuts(context, ids)
    }
}
