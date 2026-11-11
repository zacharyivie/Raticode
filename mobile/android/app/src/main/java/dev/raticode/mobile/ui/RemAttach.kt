package dev.raticode.mobile.ui

import android.Manifest
import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.ContentResolver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.DocumentsContract
import android.provider.MediaStore
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContract
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.result.contract.ActivityResultContracts.PickVisualMedia
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import dev.raticode.mobile.data.AttachPlan
import dev.raticode.mobile.data.AttachSource
import dev.raticode.mobile.data.CameraCaptures
import dev.raticode.mobile.data.OpenedFiles
import dev.raticode.mobile.share.ShareIntake
import java.io.File
import java.time.LocalDateTime

/** A file the user picked: [name] replaces the provider's name, and [photo] is a camera file to delete once read. */
internal data class Picked(val uri: Uri, val name: String? = null, val photo: File? = null)

/**
 * Files and words another screen hands to Rem's message box, such as Developer log entries or a browser page for Ask
 * Rem. The message box attaches the files and adds [text] after whatever is already typed, and puts the cursor there;
 * nothing is sent until the user sends it. [id] tells one hand-off from the next.
 */
internal data class RemHandoff(val id: Long, val files: List<Picked>, val text: String = "")

/** What the composer says about the last pick, with a Settings button when Android won't ask again. */
internal data class AttachNotice(val text: String, val settings: Boolean = false)

/**
 * Reads picked files one at a time, because the phone imports one file at once. [read] must call its callback once
 * per item, whether the file was attached or not.
 */
internal class ImportQueue<T>(private val read: (T, () -> Unit) -> Unit) {
    /** Files not yet attached, the first one being read. */
    var waiting by mutableStateOf(listOf<T>()); private set
    private var busy = false

    fun add(items: List<T>) { if (items.isEmpty()) return; waiting = waiting + items; next() }

    private fun next() {
        if (busy) return
        val item = waiting.firstOrNull() ?: return
        busy = true
        var done = false
        read(item) {
            if (!done) { done = true; busy = false; waiting = waiting.drop(1); next() }
        }
    }
}

/** Android's file browser (ACTION_OPEN_DOCUMENT), opened at [Request.start] when given. */
internal class OpenFiles : ActivityResultContract<OpenFiles.Request, List<Uri>>() {
    data class Request(val start: Uri?, val multiple: Boolean)
    override fun createIntent(context: Context, input: Request): Intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
        .addCategory(Intent.CATEGORY_OPENABLE).setType("*/*")
        .putExtra(Intent.EXTRA_ALLOW_MULTIPLE, input.multiple)
        .apply { input.start?.let { putExtra(DocumentsContract.EXTRA_INITIAL_URI, it) } }
    override fun parseResult(resultCode: Int, intent: Intent?) = picked(resultCode, intent)
}

/** Android's photo picker for up to [input] pictures and videos; it falls back to the file browser on phones without one. */
internal class PickMedia : ActivityResultContract<Int, List<Uri>>() {
    override fun createIntent(context: Context, input: Int): Intent {
        val request = PickVisualMediaRequest(PickVisualMedia.ImageAndVideo)
        return if (input >= 2) ActivityResultContracts.PickMultipleVisualMedia(input).createIntent(context, request)
        else PickVisualMedia().createIntent(context, request)
    }
    override fun parseResult(resultCode: Int, intent: Intent?) = picked(resultCode, intent)
}

private fun picked(resultCode: Int, intent: Intent?): List<Uri> {
    if (resultCode != Activity.RESULT_OK || intent == null) return emptyList()
    val clip = intent.clipData
    return (listOfNotNull(intent.data) + (0 until (clip?.itemCount ?: 0)).mapNotNull { clip?.getItemAt(it)?.uri }).distinct()
}

/** Where each file-browser entry opens. Android still lets the user move elsewhere from its menu. */
internal fun startOf(source: AttachSource): Uri? = when (source) {
    AttachSource.DOWNLOADS -> DocumentsContract.buildRootUri("com.android.providers.downloads.documents", "downloads")
    AttachSource.FILES -> DocumentsContract.buildRootUri("com.android.externalstorage.documents", "primary")
    else -> null
}

private fun cameraApp(context: Context) = context.packageManager.hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY) &&
    Intent(MediaStore.ACTION_IMAGE_CAPTURE).resolveActivity(context.packageManager) != null

/**
 * The paperclip and its menu: gallery and camera first, then the places Android's file browser opens at.
 * [room] is how many more files fit; [onPicked] gets at most that many.
 */
@Composable
internal fun AttachButton(enabled: Boolean, room: Int, onPicked: (List<Picked>) -> Unit, onNotice: (AttachNotice?) -> Unit) {
    var open by rememberSaveable { mutableStateOf(false) }
    IconButton(onClick = { onNotice(null); open = true }, enabled = enabled, modifier = Modifier.size(48.dp).testTag("remAttach")) {
        Icon(ThreadIcons.AttachFile, "Attach file")
    }
    AttachPicker(open, { open = it }, room, onPicked, onNotice)
}

/**
 * The attach menu and the pickers it opens, for a screen that draws its own attach button, such as voice mode.
 * [onLeave] runs just before another app (gallery, camera or file browser) takes the screen, and [onBack] once
 * its answer is in, whether or not anything was picked.
 */
@Composable
internal fun AttachPicker(open: Boolean, onOpen: (Boolean) -> Unit, room: Int, onPicked: (List<Picked>) -> Unit,
                          onNotice: (AttachNotice?) -> Unit, onLeave: () -> Unit = {}, onBack: () -> Unit = {}) {
    val context = LocalContext.current
    // Survives the camera app pushing Raticode out of memory, so the photo it took is still found.
    var photoPath by rememberSaveable { mutableStateOf<String?>(null) }
    val currentRoom by rememberUpdatedState(room)
    val cameraReady = remember(context) { cameraApp(context) }
    val galleryBuiltIn = remember(context) { PickVisualMedia.isPhotoPickerAvailable(context) }

    fun deliver(uris: List<Uri>) {
        onBack()
        // Neither picker returns this app's own provider, but a file Raticode serves must never be re-sent from here.
        val own = uris.filter { ShareIntake.ownAuthority(it.authority, context.packageName) { name ->
            context.packageManager.resolveContentProvider(name, 0)?.packageName } || it.scheme != ContentResolver.SCHEME_CONTENT }
        val (kept, note) = AttachPlan.fit(uris - own.toSet(), currentRoom)
        onNotice((note ?: if (own.isNotEmpty()) AttachPlan.OWN_FILE else null)?.let { AttachNotice(it) })
        if (kept.isNotEmpty()) onPicked(kept.map { Picked(it) })
    }
    val files = rememberLauncherForActivityResult(OpenFiles()) { deliver(it) }
    val media = rememberLauncherForActivityResult(PickMedia()) { deliver(it) }
    val camera = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { took ->
        onBack()
        val photo = photoPath?.let(::File)?.takeIf { CameraCaptures.owns(context.cacheDir, it) }
        photoPath = null
        when {
            photo == null -> Unit
            took && photo.length() > 0 -> {
                onNotice(null)
                onPicked(listOf(Picked(FileProvider.getUriForFile(context, context.packageName + OpenedFiles.AUTHORITY_SUFFIX, photo),
                    CameraCaptures.name(LocalDateTime.now()), photo)))
            }
            else -> {
                photo.delete()
                // A camera that reports success with an empty file failed; backing out of the camera is not a problem.
                if (took) onNotice(AttachNotice(AttachPlan.CAMERA_FAILED))
            }
        }
    }
    fun takePhoto() {
        val photo = runCatching { CameraCaptures.create(context.cacheDir) }.getOrNull()
            ?: return onNotice(AttachNotice(AttachPlan.CAMERA_FAILED))
        photoPath = photo.path
        onLeave()
        try { camera.launch(FileProvider.getUriForFile(context, context.packageName + OpenedFiles.AUTHORITY_SUFFIX, photo)) }
        catch (_: ActivityNotFoundException) { onBack(); photo.delete(); photoPath = null; onNotice(AttachNotice(AttachPlan.NO_CAMERA)) }
        catch (_: SecurityException) { onBack(); photo.delete(); photoPath = null; onNotice(AttachNotice(AttachPlan.CAMERA_DENIED, settings = true)) }
    }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        onBack()
        if (granted) takePhoto()
        else {
            // After a permanent denial Android no longer asks, so offer the app's settings page instead.
            val again = context.findActivity()?.let { ActivityCompat.shouldShowRequestPermissionRationale(it, Manifest.permission.CAMERA) } == true
            onNotice(AttachNotice(AttachPlan.CAMERA_DENIED, settings = !again))
        }
    }

    fun choose(source: AttachSource) {
        onOpen(false)
        try {
            when (source) {
                AttachSource.MEDIA -> { onLeave(); media.launch(currentRoom) }
                AttachSource.CAMERA -> when {
                    !cameraReady -> onNotice(AttachNotice(AttachPlan.NO_CAMERA))
                    // Android refuses the camera to an app that declares the camera permission without holding it.
                    ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED -> takePhoto()
                    else -> { onLeave(); permission.launch(Manifest.permission.CAMERA) }
                }
                else -> { onLeave(); files.launch(OpenFiles.Request(startOf(source), currentRoom > 1)) }
            }
        } catch (_: ActivityNotFoundException) { onBack(); onNotice(AttachNotice("No app on this phone can choose files for Raticode.")) }
    }

    if (open) AttachSheet(room, galleryBuiltIn, cameraReady, onDismiss = { onOpen(false) }, onChoose = ::choose)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AttachSheet(room: Int, galleryBuiltIn: Boolean, cameraReady: Boolean, onDismiss: () -> Unit, onChoose: (AttachSource) -> Unit) {
    ModalBottomSheet(onDismissRequest = onDismiss, modifier = Modifier.testTag("remAttachSheet")) {
        Column(Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(bottom = 16.dp)) {
            Column(Modifier.padding(horizontal = 24.dp).padding(bottom = 8.dp)) {
                Text("Attach to your message", Modifier.semantics { heading() }, style = MaterialTheme.typography.titleMedium)
                Text(AttachPlan.limit(room), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            SourceRow(ThreadIcons.Image, "Photos and videos", if (galleryBuiltIn) "Your gallery" else "Your gallery, in Android's file browser",
                "attachMedia") { onChoose(AttachSource.MEDIA) }
            SourceRow(ThreadIcons.Camera, "Camera", if (cameraReady) "Take a photo. It isn't saved to your gallery." else "No camera app on this phone",
                "attachCamera", enabled = cameraReady) { onChoose(AttachSource.CAMERA) }
            HorizontalDivider(Modifier.padding(vertical = 8.dp), color = MaterialTheme.colorScheme.outlineVariant)
            Column(Modifier.padding(horizontal = 24.dp).padding(bottom = 4.dp)) {
                Text("In Android's file browser", Modifier.semantics { heading() }, style = MaterialTheme.typography.titleSmall)
                Text("You pick the file there. If it opens somewhere else, choose the place from its menu (☰).",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            SourceRow(ThreadIcons.Recent, "Recent files", "Files you opened or saved lately", "attachRecent") { onChoose(AttachSource.RECENT) }
            SourceRow(ThreadIcons.Download, "Downloads", "Your Downloads folder", "attachDownloads") { onChoose(AttachSource.DOWNLOADS) }
            SourceRow(ThreadIcons.Folder, "All files", "Phone storage, SD card, Drive and other apps", "attachFiles") { onChoose(AttachSource.FILES) }
        }
    }
}

@Composable
private fun SourceRow(icon: ImageVector, title: String, detail: String, tag: String, enabled: Boolean = true, onClick: () -> Unit) {
    val alpha = if (enabled) 1f else 0.5f
    ListItem(
        headlineContent = { Text(title) },
        supportingContent = { Text(detail) },
        leadingContent = { Icon(icon, null, Modifier.size(24.dp)) },
        colors = ListItemDefaults.colors(
            containerColor = androidx.compose.ui.graphics.Color.Transparent,
            headlineColor = MaterialTheme.colorScheme.onSurface.copy(alpha = alpha),
            supportingColor = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = alpha),
            leadingIconColor = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = alpha)),
        modifier = Modifier.fillMaxWidth().clickable(enabled = enabled, role = Role.Button, onClick = onClick).padding(horizontal = 8.dp).testTag(tag))
}

/** Opens Raticode's page in Android's settings, where the camera permission is turned back on. */
internal fun openAppSettings(context: Context) {
    runCatching {
        context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null))
            .apply { if (context.findActivity() == null) addFlags(Intent.FLAG_ACTIVITY_NEW_TASK) })
    }
}
