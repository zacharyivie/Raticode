package dev.raticode.mobile.live

import org.json.JSONObject
import kotlin.math.roundToInt

/** One computer Raticode can run work on, as the desktop's `computers.list` reports it. */
data class Computer(
    val id: String,
    val name: String,
    val thisComputer: Boolean,
    val online: Boolean,
    /** Its worker limit; null when a paired desktop doesn't report one. */
    val maxThreads: Int?,
    val runningThreads: Int,
    val cpuPressure: Double?,
    val memoryPressure: Double?,
    /** The limit lowered by pressure: never above [maxThreads]. */
    val effectiveThreads: Int?,
    /** New workers it can take now: 0 when offline or short of memory, at most 1 while [rejoining], null when unknown. */
    val capacity: Int?,
    val rejoining: Boolean,
    /** This computer only: the Worker limit setting, where 0 picks one from cores and memory. */
    val maxThreadsSetting: Int?,
    val defaultMaxThreads: Int?,
    /** Why it takes no new worker for want of memory, such as "0.9 GB available"; null when it isn't held. */
    val memoryHold: String? = null,
    /** Running workers by kind (rem, phone, organizations, swarms); null from an older paired desktop, which shares only its total. */
    val runningByKind: Map<String, Int>? = null,
)

data class ComputerList(val computers: List<Computer>, val checkedAt: Long, val rejoinSeconds: Int) {
    val thisComputer get() = computers.firstOrNull { it.thisComputer }
}

/** Where Rem or one organization runs its workers, as the desktop's `computers.placement` reports it. */
data class Placement(
    /** `rem`, or `org:<organization ID>`. */
    val consumer: String,
    val isRem: Boolean,
    val name: String,
    /** overflow, balanced or manual. */
    val strategy: String,
    /** False until the user saves a placement for it; it then uses the default. */
    val saved: Boolean,
    /** The assigned computer IDs, for manual. */
    val computers: List<String>,
    /** Assigned computers others may also be assigned. */
    val shared: List<String>,
    /** Assigned computers that are no longer paired. */
    val missing: List<String>,
    /** Why this placement's work can't start anywhere, such as a manual pick of paired desktops only; blank when it can. */
    val notice: String = "",
)

/** One computer a placement can name: [order] lists them in the user's order. */
data class PlacedComputer(val id: String, val name: String, val online: Boolean, val thisComputer: Boolean)

data class PlacementView(val consumers: List<Placement>, val order: List<String>, val computers: List<PlacedComputer>) {
    fun computerName(id: String) = computers.firstOrNull { it.id == id }?.name ?: "Unpaired computer ${id.take(8)}"
}

/**
 * Computers: this computer and each desktop paired with it, with its state, worker limit, running workers and load.
 * The desktop owns the list; the phone only reads it and changes this computer's limit with `computers.set`.
 */
object Computers {
    const val READ = "computers.list"
    const val SET = "computers.set"
    /** The highest worker limit the desktop takes; 0 is automatic. */
    const val MAX_THREADS = 16
    const val UPDATE_DESKTOP = "Update Raticode on your computer to see its computers here."

    /** Where Rem and each organization run: read on a desktop whose overview has `computer_placement`. */
    const val PLACEMENT = "computers.placement"
    const val PLACE = "computers.place"
    const val UPDATE_PLACEMENT = "Update Raticode on your computer to choose where Rem and organizations run."
    /** The strategies in the words desktop Settings › Computers uses. */
    val STRATEGIES = listOf(
        "overflow" to "Fill one computer, then the next",
        "balanced" to "Spread evenly",
        "manual" to "Only computers I pick",
    )
    private val CONSUMER = Regex("^(rem|org:[A-Za-z0-9_-]+)$")
    private val COMPUTER_ID = Regex("^[A-Za-z0-9-]{1,64}$")

    fun isOp(op: String) = op == READ || op == SET

    /** True when [op] is a computers read or action that a desktop without the `computers` overview flag would refuse. */
    fun blocked(op: String, desktopListsComputers: Boolean) = isOp(op) && !desktopListsComputers

    /** True when [op] is a placement read or action that a desktop without `computer_placement` would refuse. */
    fun placementBlocked(op: String, desktopPlacesWork: Boolean) = (op == PLACEMENT || op == PLACE) && !desktopPlacesWork

    fun parsePlacement(json: JSONObject): PlacementView {
        // Only IDs placeParams can send back, and at most as many as one placement takes, so ticking a listed
        // computer can never fail its check and end the app.
        val computers = json.objects("computers").mapNotNull { c ->
            val id = c.optString("id")
            if (!COMPUTER_ID.matches(id)) null else PlacedComputer(id, c.optString("name").ifBlank { "Desktop" }.take(80),
                c.optString("state") == "online", c.optBoolean("this_computer"))
        }.distinctBy { it.id }.take(64)
        val known = computers.map { it.id }
        val order = json.strings("order").filter { it in known }.distinct()
        val consumers = json.objects("consumers").mapNotNull { c ->
            val consumer = c.optString("consumer").take(110)
            val strategy = c.optString("strategy").takeIf { s -> STRATEGIES.any { it.first == s } } ?: "overflow"
            if (!CONSUMER.matches(consumer)) null else Placement(
                consumer = consumer,
                isRem = consumer == "rem",
                name = c.optString("name").ifBlank { if (consumer == "rem") "Rem" else "Organization" }.take(80),
                strategy = strategy,
                saved = c.optBoolean("saved"),
                computers = c.strings("computers"),
                shared = c.strings("shared"),
                missing = c.strings("missing_computers"),
                notice = c.optString("notice").trim().take(400),
            )
        }
        return PlacementView(consumers, order + known.filter { it !in order }, computers.sortedBy { c -> order.indexOf(c.id).takeIf { it >= 0 } ?: (order.size + known.indexOf(c.id)) })
    }

    /**
     * `computers.place` for one consumer. Only manual takes [computers], at least one; [shared] must be some of them.
     * Overflow and balanced send no computers, as the desktop picks them.
     */
    fun placeParams(consumer: String, strategy: String, computers: List<String> = emptyList(), shared: List<String> = emptyList()): JSONObject {
        require(CONSUMER.matches(consumer)) { "consumer" }
        require(STRATEGIES.any { it.first == strategy }) { "strategy" }
        val params = JSONObject().put("consumer", consumer).put("strategy", strategy)
        if (strategy != "manual") return params
        val picked = computers.distinct()
        require(picked.isNotEmpty() && picked.size <= 64 && picked.all(COMPUTER_ID::matches)) { "computers" }
        params.put("computers", org.json.JSONArray(picked))
        val marked = shared.distinct().filter { it in picked }
        if (marked.isNotEmpty()) params.put("shared", org.json.JSONArray(marked))
        return params
    }

    fun strategyText(strategy: String) = STRATEGIES.firstOrNull { it.first == strategy }?.second ?: strategy

    /** The assigned computers in words, such as "Laptop (shared), Unpaired computer 0000abcd (missing)". */
    fun assignedText(placement: Placement, view: PlacementView): String = when {
        placement.strategy != "manual" -> if (placement.saved) "Any of your computers" else "Any of your computers (default)"
        else -> placement.computers.joinToString(", ") { id ->
            view.computerName(id) + (if (id in placement.shared) " (shared)" else "") + (if (id in placement.missing) " (missing)" else "")
        }
    }

    private fun JSONObject.objects(key: String): List<JSONObject> {
        val array = optJSONArray(key) ?: return emptyList()
        return (0 until minOf(array.length(), 100)).mapNotNull { array.optJSONObject(it) }
    }

    private fun JSONObject.strings(key: String): List<String> {
        val array = optJSONArray(key) ?: return emptyList()
        return (0 until minOf(array.length(), 64)).mapNotNull { (array.opt(it) as? String)?.takeIf(COMPUTER_ID::matches) }.distinct()
    }

    fun parse(json: JSONObject): ComputerList {
        val array = json.optJSONArray("computers")
        val computers = (0 until (array?.length() ?: 0)).mapNotNull { array?.optJSONObject(it) }.take(100).mapNotNull { c ->
            val id = c.optString("id").take(64)
            if (id.isBlank()) null else Computer(
                id = id,
                name = c.optString("name").ifBlank { "Desktop" }.take(80),
                thisComputer = c.optBoolean("this_computer"),
                online = c.optString("state") == "online",
                maxThreads = c.intOrNull("max_threads"),
                runningThreads = c.optInt("running_threads").coerceAtLeast(0),
                cpuPressure = c.percentOrNull("cpu_pressure"),
                memoryPressure = c.percentOrNull("memory_pressure"),
                effectiveThreads = c.intOrNull("effective_threads"),
                capacity = c.intOrNull("capacity"),
                rejoining = c.optBoolean("rejoining"),
                maxThreadsSetting = c.intOrNull("max_threads_setting")?.takeIf { it in 0..MAX_THREADS },
                defaultMaxThreads = c.intOrNull("default_max_threads"),
                memoryHold = if (c.isNull("memory_hold")) null else c.optString("memory_hold").trim().take(80).ifBlank { null },
                runningByKind = c.optJSONObject("running_by_kind")?.let { kinds ->
                    KINDS.keys.associateWith { (kinds.opt(it) as? Number)?.toInt()?.coerceAtLeast(0) ?: 0 }
                },
            )
        }
        return ComputerList(computers, json.optLong("checked_at"), json.optInt("rejoin_seconds", 60))
    }

    /** `computers.set` for this computer: [maxThreads] from 0 (automatic) to [MAX_THREADS]. */
    fun setParams(maxThreads: Int): JSONObject {
        require(maxThreads in 0..MAX_THREADS)
        return JSONObject().put("max_threads", maxThreads)
    }

    fun stateText(c: Computer) = when {
        !c.online -> "Offline"
        c.rejoining -> "Back online"
        else -> "Online"
    }

    fun limitText(c: Computer) = when {
        c.maxThreads == null -> "Not reported"
        c.maxThreadsSetting == 0 -> "${c.maxThreads} (automatic)"
        else -> c.maxThreads.toString()
    }

    /** How many new workers fit now, with why it is lower than the limit when it is. */
    fun roomText(c: Computer): String = when {
        !c.online -> "None while offline"
        c.capacity == null -> "Unknown"
        c.memoryHold != null -> "0: waiting for free memory (${c.memoryHold})"
        c.rejoining -> "${c.capacity}: one at a time just after it came back"
        c.effectiveThreads != null && c.maxThreads != null && c.effectiveThreads < c.maxThreads ->
            "${c.capacity}: pressure lowers its limit to ${c.effectiveThreads}"
        else -> c.capacity.toString()
    }

    /** Each kind of worker in words, one and many, in the order equal counts are listed (as desktop Settings › Computers). */
    val KINDS = linkedMapOf(
        "rem" to ("Rem task" to "Rem tasks"),
        "phone" to ("phone turn" to "phone turns"),
        "organizations" to ("organization worker" to "organization workers"),
        "swarms" to ("swarm turn" to "swarm turns"),
    )

    /**
     * Running workers by kind, most first, such as "2 organization workers, 1 Rem task, 1 swarm turn". Empty when
     * offline, when an older desktop shares only its total, or when the counts don't add up to it.
     */
    fun breakdownText(c: Computer): String {
        val kinds = c.runningByKind ?: return ""
        if (!c.online || kinds.values.sum() != c.runningThreads) return ""
        val order = KINDS.keys.toList()
        return kinds.filter { it.value > 0 && it.key in KINDS }.entries
            .sortedWith(compareByDescending<Map.Entry<String, Int>> { it.value }.thenBy { order.indexOf(it.key) })
            .joinToString(", ") { (kind, n) -> "$n ${if (n == 1) KINDS.getValue(kind).first else KINDS.getValue(kind).second}" }
    }

    /** "None" offline, else the count, with its breakdown when the desktop shares one: "4: 2 organization workers, 1 Rem task". */
    fun runningText(c: Computer): String {
        if (!c.online) return "None"
        val words = breakdownText(c)
        return if (words.isEmpty()) c.runningThreads.toString() else "${c.runningThreads}: $words"
    }

    fun pressureText(value: Double?) = value?.let { "${it.roundToInt()}%" } ?: "Not reported"

    private fun JSONObject.intOrNull(key: String): Int? = if (!has(key) || isNull(key)) null else (opt(key) as? Number)?.toInt()

    private fun JSONObject.percentOrNull(key: String): Double? =
        if (!has(key) || isNull(key)) null else (opt(key) as? Number)?.toDouble()?.coerceIn(0.0, 100.0)
}
