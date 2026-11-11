package dev.raticode.mobile.protocol

import com.google.gson.Strictness
import com.google.gson.stream.JsonReader
import com.google.gson.stream.JsonToken
import org.json.JSONArray
import org.json.JSONObject
import java.io.StringReader
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction

/** Strict UTF-8, unique members, bounded nesting. Uses Gson's maintained JSON tokenizer. */
internal object StrictJson {
    fun parse(bytes: ByteArray): JSONObject {
        require(bytes.size <= 65536)
        val text = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString()
        return JsonReader(StringReader(text)).use { reader ->
            reader.strictness = Strictness.STRICT
            val value = read(reader, 0)
            require(reader.peek() == JsonToken.END_DOCUMENT && value is JSONObject)
            value
        }
    }
    private fun read(reader: JsonReader, depth: Int): Any {
        require(depth <= 16)
        return when (reader.peek()) {
            JsonToken.BEGIN_OBJECT -> JSONObject().apply {
                reader.beginObject()
                val keys = mutableSetOf<String>()
                while (reader.hasNext()) {
                    require(keys.size < 1024)
                    val key = reader.nextName()
                    require(keys.add(key)) { "Duplicate JSON member" }
                    put(key, read(reader, depth + 1))
                }
                reader.endObject()
            }
            JsonToken.BEGIN_ARRAY -> JSONArray().apply {
                reader.beginArray()
                while (reader.hasNext()) { require(length() < 1024); put(read(reader, depth + 1)) }
                reader.endArray()
            }
            JsonToken.STRING -> reader.nextString()
            JsonToken.NUMBER -> reader.nextString().let { raw ->
                if (raw.matches(Regex("-?(0|[1-9][0-9]*)"))) raw.toLong() else raw.toDouble().also { require(it.isFinite()) }
            }
            JsonToken.BOOLEAN -> reader.nextBoolean()
            JsonToken.NULL -> { reader.nextNull(); JSONObject.NULL }
            else -> error("Invalid JSON value")
        }
    }
}
internal fun JSONObject.fields(vararg names: String) = require(keys().asSequence().toSet() == names.toSet())
internal fun JSONObject.string(key: String, maximum: Int): String = (get(key) as? String).also { require(it != null && it.length <= maximum) }!!
internal fun JSONObject.integer(key: String): Long = get(key).let { value ->
    require(value is Long || value is Int)
    (value as Number).toLong().also { require(it in 0..9007199254740991L) }
}
internal fun JSONObject.uuid(key: String): String = string(key, 36).also { require(java.util.UUID.fromString(it).toString() == it) }
