package dev.raticode.mobile.protocol

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

/** The rejections every desktop message, pairing code and stored record relies on before any field is read. */
class StrictJsonTest {
    private fun parse(text: String) = StrictJson.parse(text.toByteArray(Charsets.UTF_8))
    private fun rejected(block: () -> Unit) { assertThrows(Exception::class.java) { block() } }

    @Test fun aPlainObjectParses() {
        val json = parse("""{"version":2,"text":"Rem","items":[1,2.5,true,null],"nested":{"a":"b"}}""")
        assertEquals(2L, json.integer("version"))
        assertEquals("Rem", json.string("text", 10))
        assertEquals("b", json.getJSONObject("nested").getString("a"))
    }

    @Test fun duplicateKeysAreRejected() {
        rejected { parse("""{"version":1,"version":1}""") }
        rejected { parse("""{"payload":{"text":"a","text":"b"}}""") }
    }

    @Test fun invalidUtf8IsRejected() {
        rejected { StrictJson.parse(byteArrayOf(0xc3.toByte(), 0x28)) }
        // A string member holding a lone continuation byte and an overlong encoding of '/'.
        rejected { StrictJson.parse("{\"a\":\"".toByteArray() + byteArrayOf(0x80.toByte()) + "\"}".toByteArray()) }
        rejected { StrictJson.parse("{\"a\":\"".toByteArray() + byteArrayOf(0xc0.toByte(), 0xaf.toByte()) + "\"}".toByteArray()) }
    }

    @Test fun coercedNumbersAreRejected() {
        // A number written as a string stays a string, so an integer field refuses it.
        rejected { parse("""{"sequence":"0"}""").integer("sequence") }
        rejected { parse("""{"sequence":1.5}""").integer("sequence") }
        rejected { parse("""{"sequence":-1}""").integer("sequence") }
        rejected { parse("""{"sequence":9007199254740992}""").integer("sequence") }
        rejected { parse("""{"sequence":01}""") }
        rejected { parse("""{"sequence":1e400}""") }
        rejected { parse("""{"sequence":NaN}""") }
        rejected { parse("""{"text":12}""").string("text", 10) }
    }

    @Test fun oversizeAndMalformedInputIsRejected() {
        rejected { StrictJson.parse(ByteArray(65537) { ' '.code.toByte() }) }
        rejected { parse("""{"a":1} trailing""") }
        rejected { parse("""[1,2]""") }
        rejected { parse("""{'a':1}""") }
        rejected { parse("""{"a":1,}""") }
        rejected { parse("{\"a\":" + "[".repeat(17) + "]".repeat(17) + "}") }
        rejected { parse("{" + (0..1024).joinToString(",") { "\"k$it\":0" } + "}") }
    }
}
