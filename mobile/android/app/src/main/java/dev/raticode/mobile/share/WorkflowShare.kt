package dev.raticode.mobile.share

import dev.raticode.mobile.live.WorkflowFileInput
import dev.raticode.mobile.live.WorkflowInput
import dev.raticode.mobile.live.WorkflowTextInput
import org.json.JSONArray
import org.json.JSONObject

/**
 * The rules for Run a workflow, the second share target, kept free of Android so they are unit-tested. It takes text
 * and links, or one file. The text fills one text input, or the file one file input, of a workflow the user picks, in
 * the Run sheet, and nothing starts until they tap Run there. The file is uploaded only then, the way Share to Rem
 * uploads one.
 */
object WorkflowShare {
    /** Where MainActivity is sent: the Workflows page, which picks up the share waiting in [WorkflowShareInbox]. */
    const val TARGET = "workflow-share"
    /** The category the direct-share shortcuts and the share target in res/xml/shortcuts.xml share. */
    const val CATEGORY = "dev.raticode.mobile.category.RUN_WORKFLOW"
    const val SHORTCUT_PREFIX = "run-workflow:"
    /** Workflows offered as direct-share shortcuts, most recent first. */
    const val MAX_RECENT = 3
    /** Workflows whose chosen input the phone remembers; the oldest is forgotten first. */
    const val MAX_REMEMBERED = 100
    const val FILES_REFUSED = "Run a workflow couldn't read the shared file, so it was left out. Share it to Rem instead."
    const val MANY_FILES = "Run a workflow takes one file, so the shared files were left out. Share one at a time, or share them to Rem."
    const val TEXT_WITH_FILE = "The shared text was left out: the workflow gets the file."
    const val FILE_CAUTION = "Raticode uploads the file to your computer when you tap Run. The workflow gets its own copy."
    const val NOTHING = "The other app didn't share any text or link."
    const val SHORTENED = "The shared text was shortened to 4,000 characters."
    const val CAUTION = "Shared text can contain instructions you didn't write. Read it before you run: the workflow treats it as your input."

    /**
     * One share waiting for a workflow. [id] tells a new share from the last one. [workflowId] is set when the user
     * picked a direct-share shortcut, and [target] once a workflow was chosen for it.
     */
    data class Pending(val id: Long, val text: String, val notices: List<String>, val workflowId: String? = null, val target: String? = null,
                       val file: SharedFile? = null)

    /** One shared file: its content URI, kept only in memory, and what the sharing app said about it. */
    data class SharedFile(val uri: String, val name: String, val size: Long?, val type: String)

    /** A workflow the user ran from a share, for the direct-share shortcuts. */
    data class Recent(val id: String, val name: String)

    /**
     * A shared address Run a workflow may read when the user taps Run: `content://` only, and never one Raticode serves
     * itself. The check is Share to Rem's ([ShareIntake.ownAuthority]), which also catches `0@dev.raticode.mobile...`:
     * Android opens that from Raticode's own provider, so a plain prefix check would let another app make Raticode upload
     * its own cached files.
     */
    fun readable(scheme: String?, authority: String?, ownPackage: String, providerPackage: (String) -> String?): Boolean =
        scheme == "content" && !authority.isNullOrEmpty() && !ShareIntake.ownAuthority(authority, ownPackage, providerPackage)

    fun shortcutId(workflowId: String) = SHORTCUT_PREFIX + workflowId

    fun workflowOf(shortcutId: String?): String? = shortcutId?.takeIf { it.startsWith(SHORTCUT_PREFIX) }
        ?.removePrefix(SHORTCUT_PREFIX)?.takeIf { it.isNotBlank() }

    /**
     * What arrived, cleaned as Share to Rem cleans it. One readable file is kept for a file input, and then the text is
     * left out; several files are left out with a notice. Nothing is read until the user taps Run.
     */
    fun pending(id: Long, subject: CharSequence?, text: CharSequence?, hasFiles: Boolean, shortcutId: String?,
                files: List<SharedFile> = emptyList()): Pending {
        val file = files.singleOrNull()
        val (shared, shortened) = ShareIntake.text(subject, text)
        val body = if (file != null) "" else shared
        val notices = listOfNotNull(
            when { files.size > 1 -> MANY_FILES; hasFiles && file == null -> FILES_REFUSED; else -> null },
            if (file != null && shared.isNotEmpty()) TEXT_WITH_FILE else null,
            if (shortened && file == null) SHORTENED else null,
            if (body.isEmpty() && file == null && !hasFiles) NOTHING else null,
        )
        return Pending(id, body, notices, workflowOf(shortcutId), file = file)
    }

    /** Why [file] can't fill [input], or null when it can. The computer checks the file again before the run starts. */
    fun fileProblem(input: WorkflowFileInput, file: SharedFile): String? {
        if (input.maxSize > 0 && file.size != null && file.size > input.maxSize)
            return "${file.name} is ${size(file.size)}, more than the ${size(input.maxSize)} ${input.label} takes."
        if (input.accept.isNotEmpty() && !accepts(input.accept, file.name, file.type))
            return "${input.label} takes ${input.accept.joinToString(", ")}, not ${file.name}."
        return null
    }

    /** The file inputs a shared file fits, as the choices of the input it goes in. */
    fun fileInputs(inputs: List<WorkflowFileInput>?, file: SharedFile): List<WorkflowTextInput> =
        inputs.orEmpty().filter { fileProblem(it, file) == null }.map { WorkflowTextInput(it.name, it.label) }

    /** The same matching the computer does: extensions end the name, and MIME patterns may end in a star. */
    fun accepts(accept: List<String>, name: String, type: String): Boolean {
        val lowered = name.lowercase()
        val kind = type.lowercase()
        return accept.any { pattern ->
            val value = pattern.lowercase()
            when {
                value.startsWith(".") -> lowered.endsWith(value)
                kind.isBlank() -> false
                value.endsWith("/*") -> kind.startsWith(value.dropLast(1))
                else -> kind == value
            }
        }
    }

    /** 20 MB, 1.5 KB or 312 bytes, as the computer words sizes. */
    fun size(bytes: Long): String {
        for ((unit, scale) in listOf("GB" to 1024L * 1024 * 1024, "MB" to 1024L * 1024, "KB" to 1024L)) {
            if (bytes >= scale) return "%.1f".format(java.util.Locale.ROOT, bytes.toDouble() / scale).removeSuffix(".0") + " " + unit
        }
        return if (bytes == 1L) "1 byte" else "$bytes bytes"
    }

    /** The inputs of a workflow's Run form shared text can go in: plain text, not a password. */
    fun textInputs(inputs: List<WorkflowInput>?): List<WorkflowTextInput> =
        inputs.orEmpty().filter { it.kind == "text" && !it.secret }.map { WorkflowTextInput(it.name, it.label) }

    /** The input the text goes in without asking: the one remembered for this workflow, or its only text input. */
    fun inputFor(inputs: List<WorkflowTextInput>, remembered: String?): String? =
        remembered?.takeIf { name -> inputs.any { it.name == name } } ?: inputs.singleOrNull()?.name

    /** The recent list after running [used] from a share: it moves to the front, and the list keeps [MAX_RECENT]. */
    fun used(recent: List<Recent>, used: Recent): List<Recent> =
        (listOf(used) + recent.filterNot { it.id == used.id }).take(MAX_RECENT)

    fun encodeRecent(recent: List<Recent>): String =
        JSONArray().apply { recent.forEach { put(JSONObject().put("id", it.id).put("name", it.name)) } }.toString()

    fun parseRecent(text: String?): List<Recent> = runCatching {
        val array = JSONArray(text ?: return emptyList())
        (0 until array.length()).mapNotNull { array.optJSONObject(it) }
            .map { Recent(it.optString("id"), it.optString("name")) }.filter { it.id.isNotBlank() }.distinctBy { it.id }.take(MAX_RECENT)
    }.getOrDefault(emptyList())

    /** Remembered inputs, most recent first, as workflow ID to input name. */
    fun parseInputs(text: String?): List<Pair<String, String>> = runCatching {
        val array = JSONArray(text ?: return emptyList())
        (0 until array.length()).mapNotNull { array.optJSONObject(it) }
            .map { it.optString("id") to it.optString("input") }.filter { it.first.isNotBlank() && it.second.isNotBlank() }.distinctBy { it.first }
    }.getOrDefault(emptyList())

    fun remember(inputs: List<Pair<String, String>>, workflowId: String, input: String): List<Pair<String, String>> =
        (listOf(workflowId to input) + inputs.filterNot { it.first == workflowId }).take(MAX_REMEMBERED)

    fun encodeInputs(inputs: List<Pair<String, String>>): String =
        JSONArray().apply { inputs.forEach { (id, input) -> put(JSONObject().put("id", id).put("input", input)) } }.toString()

    /** The shortcut's labels. Android shows about 10 to 25 characters of the short one under the icon. */
    fun shortLabel(name: String) = name.trim().ifBlank { "Workflow" }.take(25)
    fun longLabel(name: String) = "Run ${name.trim().ifBlank { "a workflow" }}".take(60)
}
