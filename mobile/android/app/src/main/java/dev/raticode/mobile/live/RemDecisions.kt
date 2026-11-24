package dev.raticode.mobile.live

import org.json.JSONObject

/**
 * Rem's decisions on the phone: the questions Rem or a worker brought the user, each with one recommended answer and
 * the evidence the desktop checked, and the answers Rem keeps using so it doesn't ask twice. The desktop stores them
 * (rem_decisions.py) and applies every change; the phone only shows them and sends the user's choice with the revision
 * it showed, so a change made elsewhere since is refused instead of overwritten.
 */
object RemDecisions {
    const val READ = "rem.decisions"
    const val CHANGE = "rem.decision.change"
    const val UPDATE_DESKTOP = "Update Raticode on your computer to answer Rem's decisions from this phone."
    const val ANSWER_MAX = 1000
    const val REASON_MAX = 300
    /** A decision record's text sections and lists, bounded as the desktop bounds them (rem_decision_records.RecordFields). */
    const val SECTION_MAX = 1500
    const val CHECK_MAX = 600
    const val WHY_MAX = 300
    const val QUESTION_MAX = 500
    const val NOTE_MAX = 300
    const val LIST_ITEMS = 10
    const val LIST_ITEM_MAX = 200

    /** In force: the desktop's Replace with… and a record's Edit work only on these. */
    val IN_FORCE = setOf("confirmed", "provisional")

    /** Waiting for the user: a request, or Rem's own answer in self-work's auto mode, in force until the user confirms. */
    val OPEN = setOf("pending", "provisional")

    /**
     * A question past a project's three waiting cards. It has no card yet: Waiting shows only how many ([queuedLine]),
     * and it becomes pending, oldest first, when one of the waiting ones is answered, turned down or forgotten.
     */
    const val QUEUED = "queued"

    enum class Section(val label: String) { WAITING("Waiting"), DECIDED("Decided"), HISTORY("History") }

    /** Where an entry is listed, or null for a queued question, which is only counted. */
    fun section(decision: RemDecision): Section? = when (decision.status) {
        QUEUED -> null
        in OPEN -> Section.WAITING
        "confirmed" -> Section.DECIDED
        else -> Section.HISTORY
    }

    /**
     * The line under Waiting: "2 more queued", or "" when none are. Questions queued in a plan are counted under the plan
     * instead ([planQueuedLine]).
     */
    fun queuedLine(list: RemDecisionList): String {
        val inPlans = planGroups(list).sumOf { it.plan.queued }
        return (list.queued - inPlans).takeIf { it > 0 }?.let { "$it more queued" }.orEmpty()
    }

    /**
     * Plan with me: a plan's questions shown together under its title, the ones waiting for the user now. The desktop sends
     * each plan with how many of its questions are settled ([RemDecisionPlan.answered]) and how many are queued.
     */
    data class PlanGroup(val plan: RemDecisionPlan, val waiting: List<RemDecision>)

    /** The plans still asking, each with its waiting questions, in the order the desktop lists them. */
    fun planGroups(list: RemDecisionList): List<PlanGroup> = list.plans.filter { it.status == "asking" }.mapNotNull { plan ->
        val waiting = list.decisions.filter { it.plan == plan.id && section(it) == Section.WAITING }
        if (waiting.isEmpty() && plan.queued == 0) null else PlanGroup(plan, waiting)
    }

    /** Waiting questions that belong to no plan still asking: listed on their own, as before. */
    fun looseWaiting(list: RemDecisionList, waiting: List<RemDecision>): List<RemDecision> {
        val grouped = planGroups(list).map { it.plan.id }.toSet()
        return waiting.filter { it.plan !in grouped }
    }

    /** The line under a plan's title: "2 of 5 answered". */
    fun planLine(plan: RemDecisionPlan): String = "${plan.answered} of ${plan.total} answered"

    /** Under a plan's waiting questions: "2 more come up as you answer", or "". */
    fun planQueuedLine(plan: RemDecisionPlan): String = plan.queued.takeIf { it > 0 }?.let { "$it more come up as you answer" }.orEmpty()

    val PLAN_STATUSES = setOf("asking", "ready", "handed_back")

    private fun plans(json: JSONObject): List<RemDecisionPlan> = json.objects("plans").take(50).mapNotNull { p ->
        val id = p.optString("id").take(16).ifBlank { return@mapNotNull null }
        RemDecisionPlan(id, p.optString("title").take(200).ifBlank { "Plan $id" }, p.optString("goal").take(300), p.optString("project").take(200),
            p.optString("status").takeIf { it in PLAN_STATUSES } ?: "asking", p.optInt("answered").coerceAtLeast(0),
            p.optInt("total").coerceAtLeast(0), p.optInt("waiting").coerceAtLeast(0), p.optInt("queued").coerceAtLeast(0))
    }

    fun grouped(list: RemDecisionList): Map<Section, List<RemDecision>> =
        Section.entries.associateWith { section -> list.decisions.filter { section(it) == section } }

    /** The section a decision a tapped alert names is in now, or null when it is no longer listed. */
    fun sectionOf(list: RemDecisionList, id: String?): Section? = list.decisions.firstOrNull { it.id == id }?.let(::section)

    /** [items] with the decision a tapped alert opened first, the rest in their order. */
    fun focusedFirst(items: List<RemDecision>, id: String?): List<RemDecision> =
        if (id == null) items else items.sortedByDescending { it.id == id }

    /**
     * Decided, split in two: how Rem works here (facts about a project, and what Rem keeps about you) and the design
     * decisions Raticode writes as Markdown files in a project's repository.
     */
    fun decidedParts(decided: List<RemDecision>): Pair<List<RemDecision>, List<RemDecision>> = decided.partition { !it.isRecord }

    fun parse(json: JSONObject) = RemDecisionList(json.optLong("revision").coerceAtLeast(0), json.optInt("waiting").coerceAtLeast(0),
        json.optInt("provisional").coerceAtLeast(0), json.optInt("confirmed").coerceAtLeast(0),
        json.objects("decisions").take(400).mapNotNull(::decision), json.optInt("more").coerceAtLeast(0),
        parseEdits(json.optJSONArray("edits")).filter { it.status == "waiting" }, json.optInt("queued").coerceAtLeast(0), plans(json))

    /** OK and Cancel on a change the user asked Rem for in chat; the change's ID (E-n) goes as `id`. */
    const val CONFIRM_EDIT = "confirm_edit"
    const val CANCEL_EDIT = "cancel_edit"
    val EDIT_STATUSES = setOf("waiting", "applied", "cancelled", "replaced")
    /** Each side of a change, as the desktop bounds it for the phone. */
    const val EDIT_TEXT_MAX = 2200

    /**
     * The OK/Cancel cards under Rem's reply, or the waiting ones on the Decisions screen. A card names its change and the
     * decision it changes, and lists each part that changes with its text before and after; the desktop worked both
     * sides out from the decision as recorded. One with no ID, decision or known status is left out.
     */
    fun parseEdits(a: org.json.JSONArray?): List<RemDecisionEdit> = (0 until (a?.length() ?: 0)).mapNotNull { a?.optJSONObject(it) }.take(5).mapNotNull { e ->
        val id = e.optString("id").take(64).ifBlank { return@mapNotNull null }
        val decision = e.optString("decision").take(64).ifBlank { return@mapNotNull null }
        val status = e.optString("status").takeIf { it in EDIT_STATUSES } ?: return@mapNotNull null
        RemDecisionEdit(id, decision, if (e.optString("action") == "replace") "replace" else "edit", e.optString("question").take(500),
            e.optString("project").take(200), e.objects("changes").take(10).mapNotNull { c ->
                c.optString("field").take(40).ifBlank { null }?.let { field ->
                    RemDecisionEditChange(field, c.optString("label").take(60).ifBlank { field }, c.optString("before").take(EDIT_TEXT_MAX), c.optString("after").take(EDIT_TEXT_MAX))
                }
            }, status, e.optString("result").take(64), e.optString("replaced_by").take(64))
    }

    /** The card's heading: "Change D-3?" or "Replace D-3?". */
    fun editTitle(edit: RemDecisionEdit) = "${if (edit.action == "replace") "Replace" else "Change"} ${edit.decision}?"

    /** What a settled card says instead of OK and Cancel, or "" while it waits. */
    fun editSettled(edit: RemDecisionEdit): String = when (edit.status) {
        "waiting" -> ""
        "applied" -> if (edit.action == "replace" && edit.result.isNotBlank()) "Applied: ${edit.result} replaces ${edit.decision}" else "Applied to ${edit.decision}"
        "cancelled" -> "Cancelled. Nothing changed."
        "replaced" -> if (edit.replacedBy.isNotBlank()) "Replaced by a newer change (${edit.replacedBy})" else "Replaced by a newer change"
        else -> "No longer listed"
    }

    /**
     * The `rem.decision.change` params for OK ([ok]) or Cancel on change [id], or null for a blank ID. The desktop applies
     * the change only while the decision is as the card showed it, so [revision] is sent only because the contract asks.
     */
    fun editParams(id: String, ok: Boolean, revision: Long): JSONObject? {
        if (id.isBlank()) return null
        return JSONObject().put("action", if (ok) CONFIRM_EDIT else CANCEL_EDIT).put("id", id.take(64)).put("expected_revision", revision.coerceAtLeast(0))
    }

    private fun decision(d: JSONObject): RemDecision? {
        val id = d.optString("id").take(64).ifBlank { return null }
        return RemDecision(id, d.optString("topic").take(80), d.optString("question").take(500), d.optString("answer").take(ANSWER_MAX),
            d.optString("recommended").take(ANSWER_MAX), d.optString("why").take(300), evidence(d), alternatives(d),
            d.optString("status").take(20), d.optString("tried").take(1000), d.optString("supersedes").take(64),
            d.optString("superseded_by").take(64), d.optString("reopened").take(300), d.optString("project").take(200),
            d.optString("decided_by").take(20), d.optString("decided_at").take(80), d.optString("created_at").take(80),
            d.optString("updated_at").take(80), d.optBoolean("reversible", true),
            d.objects("history").takeLast(10).map { RemDecisionStep(it.optString("at").take(80), it.optString("actor").take(20),
                it.optString("action").take(40), it.optString("note").take(300)) },
            d.optJSONObject("last_check")?.let { c -> c.optString("ran_at").take(80).ifBlank { null }?.let { at ->
                RemDecisionCheck(c.optBoolean("passed"), if (c.has("exit_code") && !c.isNull("exit_code")) c.optInt("exit_code") else null, at) } },
            d.optBoolean("carried_over"), d.optString("remember_as").take(20).ifBlank { "fact" },
            d.optJSONObject("record")?.takeIf { d.optString("remember_as") == "record" }?.let(::record),
            d.optString("record_path").take(300), d.optString("record_error").take(300), d.optBoolean("edited_outside"),
            strings(d, "supersedes_all", 5, 64), d.optString("conflict").take(600),
            d.optJSONObject("path_counts")?.takeIf { d.optString("remember_as") == "record" }?.let(::pathCounts),
            d.optBoolean("record_restore") && d.optString("remember_as") == "record",
            d.optBoolean("asked_again"), if (d.optBoolean("asked_again")) d.optString("current_answer").take(ANSWER_MAX) else "",
            d.optBoolean("fresh"), d.optString("plan").take(16), d.optJSONObject("asked")?.let(::asked))
    }

    /** A waiting question's asked block; an older desktop sends none. */
    private fun asked(a: JSONObject): RemDecisionAsked {
        fun runs(key: String) = a.objects(key).take(20).map {
            RemDecisionAskedRun(it.optString("run_id").take(64), it.optString("task_id").take(64), it.optString("ticket").take(32),
                it.optString("label").take(160).ifBlank { "A task" })
        }
        return RemDecisionAsked(a.optString("label").take(160).ifBlank { "Rem" }, a.optString("ticket").take(32), a.optString("task_id").take(64),
            a.optString("task_title").take(160), a.optString("at").take(80), runs("waiting"), runs("went_on"), a.optString("opens").take(64))
    }

    private val MONTHS = listOf("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

    /** "21 Nov" for an ISO time on the phone's calendar; "" when it can't be read. */
    fun shortDay(stamp: String): String {
        val day = runCatching { java.time.OffsetDateTime.parse(stamp).atZoneSameInstant(java.time.ZoneId.systemDefault()).toLocalDate() }
            .recoverCatching { java.time.LocalDateTime.parse(stamp).toLocalDate() }.getOrNull() ?: return ""
        return "${day.dayOfMonth} ${MONTHS[day.monthValue - 1]}"
    }

    private fun runNames(runs: List<RemDecisionAskedRun>): Pair<String, Boolean> {
        val labels = runs.map { it.label }.distinct()
        return when (labels.size) {
            1 -> labels[0] to false
            2 -> "${labels[0]} and ${labels[1]}" to true
            else -> "${labels[0]} and ${labels.size - 1} more" to true
        }
    }

    /**
     * One line under a waiting question, as the desktop says it (rem_decisions.asked_line): who asked it, when, and whether the
     * runs that asked wait for it or went on without it, such as "Asked by REM-249 · 21 Nov · REM-249 waits for this". "" without one.
     */
    fun askedLine(decision: RemDecision): String {
        val asked = decision.asked ?: return ""
        val parts = mutableListOf("Asked by ${asked.label}")
        shortDay(asked.at).takeIf { it.isNotEmpty() }?.let(parts::add)
        if (asked.waiting.isNotEmpty()) {
            val (names, plural) = runNames(asked.waiting)
            parts += "$names ${if (plural) "wait" else "waits"} for this"
        } else if (asked.wentOn.isNotEmpty()) parts += "${runNames(asked.wentOn).first} went on without it"
        return parts.joinToString(" · ")
    }

    /** How many tracked files on the landing branch each record path matches; an older desktop sends none. */
    private fun pathCounts(c: JSONObject): RemDecisionPathCounts {
        fun counts(key: String): Map<String, Int> = c.optJSONObject(key)?.let { o ->
            o.keys().asSequence().take(LIST_ITEMS).mapNotNull { glob ->
                (o.opt(glob) as? Number)?.toInt()?.takeIf { it >= 0 && glob.isNotBlank() }?.let { glob.take(LIST_ITEM_MAX) to it }
            }.toMap()
        }.orEmpty()
        return RemDecisionPathCounts(c.optString("branch").take(200), counts("applies_to"), counts("forbids"))
    }

    /** What a path's count says, as the desktop says it: [warn] for an Applies to path that matches nothing or a Never path that matches files. */
    data class CountNote(val text: String, val warn: Boolean)

    fun countNote(forbids: Boolean, count: Int?): CountNote {
        if (count == null || count < 0) return CountNote("", false)
        val files = if (count == 1) "1 file" else "$count files"
        return when {
            forbids && count > 0 -> CountNote("matches $files that ${if (count == 1) "exists" else "exist"} now", true)
            forbids -> CountNote("no files now", false)
            count > 0 -> CountNote(files, false)
            else -> CountNote("matches no files", true)
        }
    }

    /**
     * The record form's warnings for the typed paths the desktop counted: an Applies to path that matches no files and a Never
     * path that matches files. A path typed since the last count has none until it is saved; warnings never stop a save.
     */
    fun pathWarnings(counts: RemDecisionPathCounts?, appliesTo: String, forbids: String): List<String> {
        counts ?: return emptyList()
        val branch = counts.branch.ifBlank { "the landing branch" }
        return splitList(appliesTo).filter { countNote(false, counts.appliesTo[it]).warn }.map { "Applies to $it matches no files on $branch." } +
            splitList(forbids).mapNotNull { glob -> countNote(true, counts.forbids[glob]).takeIf { it.warn }?.let { "Never touch $glob ${it.text}." } }
    }

    private fun record(r: JSONObject) = RemDecisionRecord(r.optString("context").take(SECTION_MAX), r.optString("options").take(SECTION_MAX),
        r.optString("consequences").take(SECTION_MAX), r.optString("check").take(CHECK_MAX), strings(r, "applies_to", LIST_ITEMS, LIST_ITEM_MAX),
        strings(r, "forbids", LIST_ITEMS, LIST_ITEM_MAX), strings(r, "signals", LIST_ITEMS, LIST_ITEM_MAX))

    private fun strings(o: JSONObject, key: String, max: Int, length: Int): List<String> = o.optJSONArray(key)?.let { a ->
        (0 until a.length()).mapNotNull { (a.opt(it) as? String)?.take(length)?.ifBlank { null } } }.orEmpty().take(max)

    /** A task update's question, or null when its message carries none or one this phone can't use. */
    fun parseCard(c: JSONObject?): RemDecisionCard? {
        c ?: return null
        if (c.optBoolean("fallback")) return RemDecisionCard(id = "", question = c.optString("question").take(500), fallback = true,
            title = c.optString("title").take(160), found = c.optString("found").take(1200), candidate = c.optString("candidate").take(ANSWER_MAX))
        val id = c.optString("id").take(64).ifBlank { return null }
        return RemDecisionCard(id, c.optString("question").take(500), c.optString("recommended").take(ANSWER_MAX), c.optString("why").take(300),
            evidence(c), alternatives(c), c.optString("status").take(20), c.optString("tried").take(1000), c.optString("supersedes").take(64),
            c.optString("reopened").take(300), if (c.has("live_status")) c.optString("live_status").take(20) else null,
            supersedesAll = strings(c, "supersedes_all", 5, 64), conflict = c.optString("conflict").take(600))
    }

    /** The topics a project scan finds. Ask again on one reads the project's files again for the new question's answer. */
    val SCAN_TOPICS = setOf("checks.fast", "branch.integration", "decisions.folder")

    /** What Ask again does, for screen readers: the decision stays in force until the user answers the new question. */
    fun askAgainDescription(topic: String): String = "Ask again: " +
        (if (topic in SCAN_TOPICS) "Rem reads the project's files again and asks you with what they say now"
        else "this comes back as a new question for you") + ". This answer stays in force until you choose."

    /** Why a waiting question is back: "You asked again" for the user's Ask again, else the trigger, or "". */
    fun reopenedLine(decision: RemDecision): String = when {
        decision.askedAgain && decision.fresh -> "You asked again. Rem read the project's files again."
        decision.askedAgain -> "You asked again"
        decision.reopened.isNotBlank() -> "Asked again: ${decision.reopened}"
        else -> ""
    }

    /** What a conflict's fix replaces, as a short line: "Replaces D-3, D-5", or "" for an ordinary request. */
    fun replacesLine(ids: List<String>): String = if (ids.isEmpty()) "" else "Replaces " + ids.joinToString(", ")

    private fun evidence(d: JSONObject) = d.objects("evidence").take(5).map {
        RemDecisionEvidence(it.optString("kind").take(20), it.optString("ref").take(500), it.optString("excerpt").take(500))
    }.filter { it.ref.isNotBlank() }

    private fun alternatives(d: JSONObject) = d.objects("alternatives").take(3).map {
        RemDecisionAlternative(it.optString("answer").take(ANSWER_MAX), it.optString("why_not").take(300))
    }.filter { it.answer.isNotBlank() }

    private val EVIDENCE_LABELS = mapOf("file" to "file", "quote" to "quote", "command" to "ran", "commit" to "commit", "doc" to "doc")

    /** The evidence as one line, as on the desktop: "quote package.json · ran npm test · +1 more". */
    fun evidenceLine(evidence: List<RemDecisionEvidence>, max: Int = 3): String {
        val shown = evidence.take(max).map { "${EVIDENCE_LABELS[it.kind] ?: it.kind} ${it.ref}".trim() }
        val more = evidence.size - shown.size
        return (shown + listOfNotNull(if (more > 0) "+$more more" else null)).joinToString(" · ")
    }

    /** What a decided entry says under its answer: who decided and on what day. */
    fun decidedLine(decision: RemDecision): String {
        val who = when {
            decision.carriedOver -> "Carried over"
            decision.decidedBy == "user" -> "You decided"
            decision.decidedBy == "rem" -> "Rem decided (auto mode)"
            else -> ""
        }
        val day = decision.decidedAt.take(10)
        return listOf(who + if (who.isNotBlank() && day.isNotBlank()) " on $day" else "", decision.project.ifBlank { "About you" }, decision.id)
            .filter { it.isNotBlank() }.joinToString(" · ")
    }

    private val HISTORY_ACTORS = mapOf("user" to "You", "rem" to "Rem", "raticode" to "Raticode")
    /** What rem_decisions and rem_decision_records write, as the desktop words it (remDecisions.js historyEntries). */
    private val HISTORY_ACTIONS = mapOf(
        "requested" to "Asked", "queued" to "Queued", "provisional" to "Decided for now (auto mode)", "moved up" to "Moved up to waiting",
        "conflict found" to "Found a conflict", "added" to "Added", "confirmed" to "Confirmed", "changed" to "Changed the answer",
        "edited" to "Edited", "rejected" to "Turned down", "retired" to "Retired", "reopened" to "Asked again", "superseded" to "Replaced",
        "rechecked" to "Checked again", "carried over" to "Carried over", "kept the approved text" to "Kept the approved text",
        "read" to "Read from the repository", "edited outside" to "Edited outside Raticode",
    )
    private val DECISION_ID = Regex("D-\\d+")

    /** One row of a decision's History: the [date], who ([actor]: You, Rem or Raticode), what they did and an optional [note]. */
    data class HistoryRow(val date: String, val actor: String, val action: String, val note: String)

    /**
     * A decision's history as the desktop lists it, newest first. A user change made without a reason records only where it
     * came from ("user"), so that note is left out; a replacement names the decision that replaced it.
     */
    fun historyRows(decision: RemDecision): List<HistoryRow> = decision.history.filter { it.action.isNotBlank() }.map { step ->
        var action = HISTORY_ACTIONS[step.action] ?: step.action.replaceFirstChar { it.uppercase() }
        var note = step.note.trim()
        if (step.actor == "user" && note == "user") note = ""
        if (step.action == "superseded" && DECISION_ID.matches(note)) { action = "Replaced by $note"; note = "" }
        HistoryRow(step.at.take(10), HISTORY_ACTORS[step.actor] ?: step.actor.replaceFirstChar { it.uppercase() }, action, note)
    }.reversed()

    const val RESTORE_NOTE = "The file differs from the approved text; Raticode puts it back when the checkout is clean"

    /**
     * What a design decision's page says about its file, as (title, text), or null when nothing needs saying: a Rem run's
     * change waiting to be put back ([RemDecision.recordRestore]) or a decision in force that isn't written yet.
     */
    fun fileNote(decision: RemDecision): Pair<String, String>? = when {
        !decision.isRecord -> null
        decision.recordRestore -> "File differs" to RESTORE_NOTE
        decision.recordPath.isBlank() && decision.status in IN_FORCE ->
            "Not in the repo yet" to decision.recordError.ifBlank { "Raticode writes it to the project when it can." }
        else -> null
    }

    /** Raticode's own last run of a decided check command, or "" when it never ran one. */
    fun checkLine(decision: RemDecision): String {
        val check = decision.lastCheck ?: return ""
        val state = if (check.passed) "passed" else "failed" + (check.exitCode?.let { " (exit $it)" } ?: "")
        return "Raticode's last run of it $state on ${check.ranAt.take(10)}"
    }

    /** A closed entry's label. */
    fun closedLabel(decision: RemDecision): String = when (decision.status) {
        "superseded" -> if (decision.supersededBy.isNotBlank()) "Replaced by ${decision.supersededBy}" else "Replaced"
        "rejected" -> if (retired(decision)) "Retired" else "Turned down"
        else -> ""
    }

    /**
     * A design decision whose file is in the repository. The desktop retires it instead of forgetting it: it stays turned
     * down and its file is rewritten with status: rejected, since deleting it would let the file bring it back.
     */
    fun written(decision: RemDecision): Boolean = decision.isRecord && decision.recordPath.isNotBlank()

    /** A written record the user retired; its history says so. */
    fun retired(decision: RemDecision): Boolean =
        decision.status == "rejected" && decision.history.any { it.action == "retired" }

    /** Retire for a written record, Forget for the rest. */
    fun removalLabel(decision: RemDecision): String = if (written(decision)) "Retire" else "Forget"

    /**
     * The action [removalLabel] sends. Retiring commits the record's file on the landing branch, so it is its own action:
     * the desktop refuses "forget" for a written record rather than turning it into that commit.
     */
    fun removalAction(decision: RemDecision): String = if (written(decision)) "retire" else "forget"

    /** The answer a decision shows: the one in force, else Rem's recommendation. */
    fun answer(decision: RemDecision) = decision.answer.ifBlank { decision.recommended }

    /**
     * A chat card's status now and the revision to send with a change. Both the chat (`rem.home`) and the Decisions
     * screen (`rem.decisions`) report it; the newer of the two counts. An older desktop sends neither, so the card's own
     * status stands, with no revision.
     */
    fun live(card: RemDecisionCard, home: RemHome?, list: RemDecisionList?): Pair<String?, Long?> {
        val fromHome = home?.takeIf { it.decisions }?.let { (card.liveStatus ?: card.status) to it.decisionsRevision }
        val listed = list?.decisions?.firstOrNull { it.id == card.id }
        val fromList = list?.let { (listed?.status ?: "") to it.revision }
        val newest = listOfNotNull(fromHome, fromList).maxByOrNull { it.second } ?: return card.status to null
        return newest.first.ifBlank { null } to newest.second
    }

    /** What a chat card shows now: its buttons while it waits, or how it was settled. */
    sealed interface CardState {
        data object Fallback : CardState
        data class Open(val provisional: Boolean) : CardState
        data class Decided(val replaced: Boolean) : CardState
        /** Behind the questions already waiting; its buttons show once it moves up. */
        data object Queued : CardState
        data object TurnedDown : CardState
        /** The user forgot it while it waited; the desktop marks the card itself, since the list no longer has it. */
        data object Withdrawn : CardState
        data object Gone : CardState
    }

    const val WITHDRAWN = "withdrawn"

    fun cardState(card: RemDecisionCard, status: String?): CardState = when {
        card.fallback -> CardState.Fallback
        status == WITHDRAWN || (status == null && card.status == WITHDRAWN) -> CardState.Withdrawn
        status == null -> CardState.Gone
        status in OPEN -> CardState.Open(status == "provisional")
        status == QUEUED -> CardState.Queued
        status == "confirmed" -> CardState.Decided(false)
        status == "superseded" -> CardState.Decided(true)
        status == "rejected" -> CardState.TurnedDown
        else -> CardState.Gone
    }

    /**
     * The `rem.decision.change` params for [action] on [id], with the revision the phone showed, or null when there is
     * nothing to send: Change and Edit need an answer. Text is trimmed and bounded as the desktop bounds it.
     */
    fun changeParams(action: String, id: String, revision: Long, answer: String? = null, reason: String? = null): JSONObject? {
        if (action !in setOf("use", "change", "edit", "reject", "reopen", "forget", "retire", "scan") || id.isBlank()) return null
        val params = JSONObject().put("action", action).put("id", id).put("expected_revision", revision)
        if (action == "change" || action == "edit") {
            val text = answer.orEmpty().trim().take(ANSWER_MAX).ifBlank { return null }
            params.put("answer", text)
        }
        reason?.trim()?.take(REASON_MAX)?.takeIf { it.isNotBlank() && action == "reject" }?.let { params.put("reason", it) }
        return params
    }

    /** A list field as the user types it: path globs or words separated by commas or new lines. */
    fun splitList(text: String): List<String> = text.split(',', '\n').map { it.trim() }.filter { it.isNotEmpty() }.distinct()

    /** Why a typed list can't be sent, or null when it can: at most [LIST_ITEMS] entries of [LIST_ITEM_MAX] characters. */
    fun listProblem(text: String): String? {
        val items = splitList(text)
        return when {
            items.size > LIST_ITEMS -> "Keep it to $LIST_ITEMS entries."
            items.any { it.length > LIST_ITEM_MAX } -> "Keep each entry to $LIST_ITEM_MAX characters."
            else -> null
        }
    }

    /** A record's parts as the phone shows them in Edit and Replace with…: the lists joined with commas. */
    fun draft(decision: RemDecision, answer: String = answer(decision)): RemDecisionDraft {
        val r = decision.record ?: RemDecisionRecord()
        return RemDecisionDraft(answer, decision.why, r.context, r.options, r.consequences, r.check, r.appliesTo.joinToString(", "),
            r.forbids.joinToString(", "))
    }

    /**
     * The `rem.decision.change` params for a record's Edit or Replace with… on [decision], or null when there is nothing to
     * send. Edit sends only what changed from what the phone showed, so a part the desktop shortened or kept a folder out
     * of is never written back; it needs an answer and at least one change. Replace with… needs a new answer and makes a
     * new decision in place of the one in force; its record parts start from the old one, so it too sends only the ones
     * changed. A list that is too long is refused here rather than cut.
     */
    fun recordChangeParams(action: String, decision: RemDecision, revision: Long, draft: RemDecisionDraft, note: String = ""): JSONObject? {
        if (action != "edit" && action != "replace") return null
        if (decision.status !in IN_FORCE && action == "replace") return null
        if (listProblem(draft.appliesTo) != null || listProblem(draft.forbids) != null) return null
        val shown = draft(decision)
        val params = JSONObject().put("action", action).put("id", decision.id).put("expected_revision", revision)
        val answer = draft.answer.trim().take(ANSWER_MAX).ifBlank { return null }
        var changed = false
        fun text(key: String, value: String, before: String, max: Int, always: Boolean = false) {
            val now = value.trim().take(max)
            if (always || now != before.trim().take(max)) { params.put(key, now); changed = true }
        }
        if (action == "replace") params.put("answer", answer).also { changed = true }
        else text("answer", answer, shown.answer, ANSWER_MAX)
        if (action == "replace") { if (draft.why.isNotBlank()) params.put("why", draft.why.trim().take(WHY_MAX)) }
        else text("why", draft.why, shown.why, WHY_MAX)
        if (decision.isRecord) {
            text("context", draft.context, shown.context, SECTION_MAX)
            text("options", draft.options, shown.options, SECTION_MAX)
            text("consequences", draft.consequences, shown.consequences, SECTION_MAX)
            text("check", draft.check, shown.check, CHECK_MAX)
            for ((key, value, before) in listOf(Triple("applies_to", draft.appliesTo, shown.appliesTo), Triple("forbids", draft.forbids, shown.forbids))) {
                val now = splitList(value)
                if (now != splitList(before)) { params.put(key, org.json.JSONArray(now)); changed = true }
            }
        }
        if (!changed) return null
        note.trim().take(NOTE_MAX).takeIf { it.isNotBlank() }?.let { params.put("note", it) }
        return params
    }

    private fun JSONObject.objects(key: String): List<JSONObject> = optJSONArray(key)?.let { a -> (0 until a.length()).mapNotNull { a.optJSONObject(it) } }.orEmpty()
}

/** One piece of checked evidence: [kind] is file, quote, command, commit or doc; [ref] never names a folder outside the project. */
data class RemDecisionEvidence(val kind: String, val ref: String, val excerpt: String)
/** An answer Rem considered and why it didn't recommend it. */
data class RemDecisionAlternative(val answer: String, val whyNot: String)
/** Raticode's own last run of a decided check command: whether it [passed], its exit code and when it ran. */
data class RemDecisionCheck(val passed: Boolean, val exitCode: Int?, val ranAt: String)
/** One change in a decision's history: who ([actor] user, rem or raticode) did what, with an optional note such as a reason. */
data class RemDecisionStep(val at: String, val actor: String, val action: String, val note: String)
/**
 * One of Rem's decisions. [status] is pending (waits for the user), queued (waits behind a project's three pending
 * ones, with no card yet), provisional (Rem applied its recommendation in
 * self-work's auto mode; in force until the user confirms or changes it), confirmed, superseded or rejected. [project]
 * is a project's name, empty for a decision about the user. [carriedOver] marks one Raticode recorded from what it already
 * did before decisions existed, such as landing on dev. [recordRestore] marks a record whose file a Rem run changed: Raticode
 * puts the approved text back once the checkout is clean. [askedAgain] marks the question the user's Ask again filed: the
 * decision it supersedes stays in force, with [currentAnswer], until it is answered; [fresh] says a project scan gave its
 * recommendation.
 */
data class RemDecision(val id: String, val topic: String, val question: String, val answer: String, val recommended: String,
                       val why: String, val evidence: List<RemDecisionEvidence>, val alternatives: List<RemDecisionAlternative>,
                       val status: String, val tried: String, val supersedes: String, val supersededBy: String, val reopened: String,
                       val project: String, val decidedBy: String, val decidedAt: String, val createdAt: String, val updatedAt: String,
                       val reversible: Boolean, val history: List<RemDecisionStep>, val lastCheck: RemDecisionCheck? = null,
                       val carriedOver: Boolean = false, val rememberAs: String = "fact", val record: RemDecisionRecord? = null,
                       val recordPath: String = "", val recordError: String = "", val editedOutside: Boolean = false,
                       val supersedesAll: List<String> = emptyList(), val conflict: String = "",
                       val pathCounts: RemDecisionPathCounts? = null, val recordRestore: Boolean = false,
                       val askedAgain: Boolean = false, val currentAnswer: String = "", val fresh: Boolean = false,
                       val plan: String = "", val asked: RemDecisionAsked? = null) {
    /** A design decision Raticode writes as a Markdown file in the project's repository ([rememberAs] record). */
    val isRecord: Boolean get() = record != null
}
/**
 * Who asked a waiting question ([label]: a ticket key, a task's title, Self-work, Rem's chat, Project scan, you or Rem), when
 * ([at]), the runs that asked it that wait for it now ([waiting]) or went on without it ([wentOn]), and the task whose sheet the
 * asked line opens ([opens], blank for none).
 */
data class RemDecisionAsked(val label: String, val ticket: String = "", val taskId: String = "", val taskTitle: String = "", val at: String = "",
                            val waiting: List<RemDecisionAskedRun> = emptyList(), val wentOn: List<RemDecisionAskedRun> = emptyList(),
                            val opens: String = "")
/** A run that asked a waiting question, named by its ticket key or task title ([label]). */
data class RemDecisionAskedRun(val runId: String, val taskId: String, val ticket: String, val label: String)
/**
 * A decision record's parts beyond its question and answer: [context], the [options] not chosen, [consequences], how a
 * change is [check]ed against it, the path globs it [appliesTo], the globs a change must never add ([forbids]) and words
 * that suggest a proposal contradicts it ([signals]).
 */
data class RemDecisionRecord(val context: String = "", val options: String = "", val consequences: String = "", val check: String = "",
                             val appliesTo: List<String> = emptyList(), val forbids: List<String> = emptyList(), val signals: List<String> = emptyList())
/** Tracked files on [branch]'s tip that each record path matches, by path glob: the record's [appliesTo] and [forbids] paths. */
data class RemDecisionPathCounts(val branch: String = "", val appliesTo: Map<String, Int> = emptyMap(), val forbids: Map<String, Int> = emptyMap())
/** What the user types in a record's Edit or Replace with…: one field per part, the lists as comma-separated text. */
data class RemDecisionDraft(val answer: String = "", val why: String = "", val context: String = "", val options: String = "",
                            val consequences: String = "", val check: String = "", val appliesTo: String = "", val forbids: String = "")
/**
 * The Decisions screen. [revision] goes back with every change; [more] counts entries left out to fit one answer; [queued]
 * counts questions past a project's three waiting cards, which have no card yet.
 */
data class RemDecisionList(val revision: Long, val waiting: Int, val provisional: Int, val confirmed: Int, val decisions: List<RemDecision>,
                           val more: Int = 0, val edits: List<RemDecisionEdit> = emptyList(), val queued: Int = 0,
                           val plans: List<RemDecisionPlan> = emptyList())
/**
 * A Plan with me plan: the user asked Rem to plan [title] with them, and Rem's questions wait as decisions tagged with [id].
 * [answered] of [total] are settled; [waiting] have a card and [queued] come up as the user answers. [status] is asking, ready
 * (all settled) or handed_back (Rem got the answers back to file the work). [project] is a project's name.
 */
data class RemDecisionPlan(val id: String, val title: String, val goal: String = "", val project: String = "", val status: String = "asking",
                           val answered: Int = 0, val total: Int = 0, val waiting: Int = 0, val queued: Int = 0)
/** One part of a decision a chat change rewrites: its [label], and its text [before] and [after]. Empty means the part is blank. */
data class RemDecisionEditChange(val field: String, val label: String, val before: String, val after: String)
/**
 * A change to decision [decision] the user asked Rem for in chat, applied only when they press OK. [action] is edit or
 * replace (a new decision in its place); [status] is waiting, applied, cancelled or replaced (by a newer change,
 * [replacedBy]). [result] names the decision OK made. [project] is a project's name.
 */
data class RemDecisionEdit(val id: String, val decision: String, val action: String, val question: String, val project: String,
                           val changes: List<RemDecisionEditChange>, val status: String, val result: String = "", val replacedBy: String = "") {
    val waiting: Boolean get() = status == "waiting"
}
/**
 * The question a Rem task update brings, as written when the update was posted. [liveStatus] is its status now, from a
 * desktop that sends it, and empty once the decision was forgotten. A [fallback] card is Raticode's own when Rem's
 * request couldn't be used: it has no ID or buttons, only the question, what the worker [found] and an unchecked [candidate].
 */
data class RemDecisionCard(val id: String, val question: String, val recommended: String = "", val why: String = "",
                           val evidence: List<RemDecisionEvidence> = emptyList(), val alternatives: List<RemDecisionAlternative> = emptyList(),
                           val status: String = "pending", val tried: String = "", val supersedes: String = "", val reopened: String = "",
                           val liveStatus: String? = null, val fallback: Boolean = false, val title: String = "", val found: String = "",
                           val candidate: String = "", val supersedesAll: List<String> = emptyList(), val conflict: String = "")
