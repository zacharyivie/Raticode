package dev.raticode.mobile.live

import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale
import java.util.UUID

/**
 * A contact and one of their numbers that the user picked with Android's contact picker, with the words they use for
 * that person ("brother", "Baby Bro"). It lives only on this phone: the number, contact name and lookup key never go to
 * the desktop or to Rem, which only ever sends the user's own words for the person.
 */
data class SavedPerson(val id: String, val contactName: String, val number: String, val label: String, val lookupKey: String,
                       val names: List<String>, val savedAt: Long)

/** What the contact picker returned: one person and the one number the user chose. */
data class PickedContact(val contactName: String, val number: String, val label: String, val lookupKey: String)

/** How a call or text card naming a person stands: who it opens, which saved people it could mean, or nobody yet. */
sealed interface PersonMatch {
    data class One(val person: SavedPerson) : PersonMatch
    data class Several(val people: List<SavedPerson>) : PersonMatch
    data object None : PersonMatch
}

/**
 * Rules for remembered people. Matching is plain and predictable: the words are lowercased, a leading "my" or "the" and
 * punctuation are dropped, and only exact matches with a saved name or the contact's own name count. A misheard name
 * therefore finds nobody and the user picks, rather than reaching a stranger.
 */
object PhonePeople {
    const val MAX_PEOPLE = 200
    const val MAX_NAMES = 10
    const val MAX_NAME = 60
    /** Choices shown at once, as Apple advises for picking among matches. */
    const val MAX_CHOICES = 5

    /** The comparable form of a name, or "" when nothing is left. */
    fun key(words: String): String {
        val plain = words.lowercase(Locale.ROOT).replace(Regex("[’']s\\b"), "").replace(Regex("[^\\p{L}\\p{N}]+"), " ").trim()
        return plain.replace(Regex("^(my|the)\\b\\s*"), "").trim()
    }

    /** The saved people [words] may mean, newest first. */
    fun matches(people: List<SavedPerson>, words: String): List<SavedPerson> {
        val wanted = key(words)
        if (wanted.isEmpty()) return emptyList()
        return people.filter { person -> (person.names + person.contactName).any { key(it) == wanted } }.sortedByDescending { it.savedAt }
    }

    /** One person to open, several to choose from, or none. A person the user just picked for this card wins. */
    fun resolve(people: List<SavedPerson>, words: String, chosen: SavedPerson? = null): PersonMatch {
        chosen?.let { return PersonMatch.One(it) }
        val found = matches(people, words)
        return when (found.size) {
            0 -> PersonMatch.None
            1 -> PersonMatch.One(found.single())
            else -> PersonMatch.Several(found.take(MAX_CHOICES))
        }
    }

    /**
     * The picked number as cards may open it, or null. The user chose it, but it still has to pass the same rule as a
     * number Rem passes along: no service codes, pauses or extensions, which could act inside the dialer.
     */
    fun usableNumber(raw: String): String? {
        // Contacts apps store numbers with non-breaking spaces and hyphens; those become plain ones first.
        val cleaned = raw.map { c -> when { c.isWhitespace() -> ' '; c == '\u2011' || c == '\u2010' || c == '\u2013' -> '-'; else -> c } }
            .joinToString("").replace(Regex(" +"), " ").trim()
        return cleaned.takeIf { PhoneActions.validNumber(it) }
    }

    /** The last four digits, so two numbers with the same label can be told apart on the card. */
    fun ending(number: String): String = number.filter { it.isDigit() }.takeLast(4)

    /** "Baby Bro · Mobile". */
    fun describe(person: SavedPerson): String = listOf(person.contactName, person.label).filter { it.isNotBlank() }.joinToString(" · ")

    /**
     * Adds what the user picked. The same contact and number gains [name] instead of becoming a second entry. Other people
     * keep the name too, so two brothers both answer to "brother" and the card asks which one.
     */
    fun remember(people: List<SavedPerson>, picked: PickedContact, name: String?, now: Long, id: () -> String = { UUID.randomUUID().toString() }): List<SavedPerson> {
        val number = requireNotNull(usableNumber(picked.number))
        val words = name?.trim()?.take(MAX_NAME)?.takeIf { key(it).isNotEmpty() }
        val existing = people.firstOrNull { same(it, picked, number) }
        if (existing != null) {
            val names = if (words == null || existing.names.any { key(it) == key(words) }) existing.names else (existing.names + words).takeLast(MAX_NAMES)
            return people.map { if (it.id == existing.id) it.copy(names = names, contactName = picked.contactName, label = picked.label, savedAt = now) else it }
        }
        val person = SavedPerson(id(), picked.contactName.take(MAX_NAME * 2), number, picked.label.take(MAX_NAME), picked.lookupKey,
            listOfNotNull(words), now)
        return (people + person).sortedByDescending { it.savedAt }.take(MAX_PEOPLE)
    }

    /** The person picked for a card, for this time only; it isn't saved. */
    fun once(picked: PickedContact, now: Long): SavedPerson =
        SavedPerson("once", picked.contactName, requireNotNull(usableNumber(picked.number)), picked.label, picked.lookupKey, emptyList(), now)

    /** Points [id] at a newly picked contact or number, keeping the names the user gave. */
    fun change(people: List<SavedPerson>, id: String, picked: PickedContact, now: Long): List<SavedPerson> {
        val number = requireNotNull(usableNumber(picked.number))
        return people.map { if (it.id == id) it.copy(contactName = picked.contactName, number = number, label = picked.label, lookupKey = picked.lookupKey, savedAt = now) else it }
    }

    fun addName(people: List<SavedPerson>, id: String, name: String): List<SavedPerson> {
        val words = name.trim().take(MAX_NAME)
        if (key(words).isEmpty()) return people
        return people.map { p -> if (p.id != id || p.names.any { key(it) == key(words) }) p else p.copy(names = (p.names + words).takeLast(MAX_NAMES)) }
    }

    fun removeName(people: List<SavedPerson>, id: String, name: String): List<SavedPerson> =
        people.map { p -> if (p.id == id) p.copy(names = p.names.filter { it != name }) else p }

    fun forget(people: List<SavedPerson>, id: String): List<SavedPerson> = people.filter { it.id != id }

    private fun same(person: SavedPerson, picked: PickedContact, number: String) =
        digits(person.number) == digits(number) && (picked.lookupKey.isEmpty() || person.lookupKey.isEmpty() || person.lookupKey == picked.lookupKey)

    private fun digits(number: String) = number.filter { it.isDigit() }

    fun encode(people: List<SavedPerson>): String = JSONObject().put("version", 1).put("people", JSONArray().apply {
        people.forEach { p ->
            put(JSONObject().put("id", p.id).put("contact_name", p.contactName).put("number", p.number).put("label", p.label)
                .put("lookup_key", p.lookupKey).put("names", JSONArray(p.names)).put("saved_at", p.savedAt))
        }
    }).toString()

    /** Saved people from [text]; entries that no longer pass the rules are dropped rather than trusted. */
    fun decode(text: String): List<SavedPerson> {
        val list = JSONObject(text).optJSONArray("people") ?: return emptyList()
        return (0 until list.length()).mapNotNull { i ->
            val o = list.optJSONObject(i) ?: return@mapNotNull null
            val number = usableNumber(o.optString("number")) ?: return@mapNotNull null
            val names = o.optJSONArray("names")?.let { a -> (0 until a.length()).map { a.optString(it) } }.orEmpty()
                .filter { key(it).isNotEmpty() }.map { it.take(MAX_NAME) }.take(MAX_NAMES)
            SavedPerson(o.optString("id").ifEmpty { return@mapNotNull null }, o.optString("contact_name"), number, o.optString("label"),
                o.optString("lookup_key"), names, o.optLong("saved_at"))
        }.take(MAX_PEOPLE)
    }
}
