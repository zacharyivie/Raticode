package dev.raticode.mobile.browser

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64

/** Files a page builds itself (blob: and data: links), such as a chat Open WebUI exports. */
class PageFilesTest {
    private val html = "<!doctype html><title>Chat</title><p>Exported chat</p>"

    @Test fun onlyASecurePagesOwnFilesCanBeSaved() {
        assertTrue(PageFiles.isPageFile("blob:https://chat.example.com/5c7b"))
        assertTrue(PageFiles.isPageFile("DATA:text/html,x"))
        assertFalse(PageFiles.isPageFile("https://example.com/a.html"))
        assertTrue(PageFiles.allowed("blob:https://chat.example.com/5c7b", "https://chat.example.com/c/1"))
        assertFalse(PageFiles.allowed("blob:http://chat.example.com/5c7b", "https://chat.example.com/"))
        assertFalse(PageFiles.allowed("blob:null/5c7b", "https://chat.example.com/"))
        assertTrue(PageFiles.allowed("data:text/html,x", "https://chat.example.com/"))
        assertFalse(PageFiles.allowed("data:text/html,x", "about:blank"))
        assertFalse(PageFiles.allowed("data:text/html,x", "http://chat.example.com/"))
        assertFalse(PageFiles.allowed("https://chat.example.com/a.html", "https://chat.example.com/"))
        assertEquals("chat.example.com", PageFiles.host("blob:https://chat.example.com/5c7b", "https://other.example/"))
        assertEquals("a page on chat.example.com", PageFiles.host("data:text/html,x", "https://www.chat.example.com/"))
    }

    @Test fun geckoFollowsOnlyAPagesLinksToItsOwnFiles() {
        val page = "https://chat.example.com/c/1"
        assertTrue(PageFiles.geckoMayLoad("blob:https://chat.example.com/5c7b", page))
        assertTrue(PageFiles.geckoMayLoad("blob:https://chat.example.com:443/5c7b", page))
        assertTrue(PageFiles.geckoMayLoad("blob:http://127.0.0.1:4000/5c7b", "http://127.0.0.1:4000/"))
        assertTrue(PageFiles.geckoMayLoad("data:text/html,x", page))
        // Another site's blob, another port or scheme, or no page behind the link.
        assertFalse(PageFiles.geckoMayLoad("blob:https://evil.example/5c7b", page))
        assertFalse(PageFiles.geckoMayLoad("blob:https://chat.example.com:8443/5c7b", page))
        assertFalse(PageFiles.geckoMayLoad("blob:http://chat.example.com/5c7b", page))
        assertFalse(PageFiles.geckoMayLoad("blob:null/5c7b", page))
        assertFalse(PageFiles.geckoMayLoad("blob:https://chat.example.com/5c7b", null))
        assertFalse(PageFiles.geckoMayLoad("data:text/html,x", null))
        assertFalse(PageFiles.geckoMayLoad("data:text/html,x", "about:blank"))
        assertFalse(PageFiles.geckoMayLoad("blob:https://chat.example.com/5c7b", "data:text/html,x"))
        // Everything else still goes through the browser's own rules.
        assertFalse(PageFiles.geckoMayLoad("javascript:alert(1)", page))
        assertFalse(PageFiles.geckoMayLoad("https://chat.example.com/next", page))
    }

    @Test fun dataAddressesDecodeInEveryForm() {
        val plain = PageFiles.parseDataUrl("data:text/html;charset=utf-8," + java.net.URLEncoder.encode(html, "UTF-8").replace("+", "%20"))!!
        assertEquals("text/html", plain.mime)
        assertEquals(html, plain.bytes.toString(Charsets.UTF_8))
        val encoded = Base64.getEncoder().encodeToString(html.toByteArray())
        val base64 = PageFiles.parseDataUrl("data:text/html;base64,$encoded")!!
        assertEquals(html, base64.bytes.toString(Charsets.UTF_8))
        // Characters a page didn't escape, and a missing type.
        assertEquals("héllo <b>", PageFiles.parseDataUrl("data:,héllo <b>")!!.bytes.toString(Charsets.UTF_8))
        assertEquals("text/plain", PageFiles.parseDataUrl("data:,x")!!.mime)
        assertEquals("text/plain", PageFiles.parseDataUrl("data:not a type,x")!!.mime)
        val binary = byteArrayOf(0, -1, 10, 13, 37)
        assertArrayEquals(binary, PageFiles.parseDataUrl("data:application/octet-stream;base64," + Base64.getEncoder().encodeToString(binary))!!.bytes)
    }

    @Test fun brokenOrOversizedDataAddressesAreRefused() {
        assertNull(PageFiles.parseDataUrl("data:text/html"))
        assertNull(PageFiles.parseDataUrl("data:text/html,%G1"))
        assertNull(PageFiles.parseDataUrl("data:text/html,abc%2"))
        assertNull(PageFiles.parseDataUrl("data:text/html;base64,***"))
        assertNull(PageFiles.parseDataUrl("https://example.com/"))
        assertNull(PageFiles.parseDataUrl("data:text/plain," + "x".repeat(101), max = 100))
        assertNull(PageFiles.parseDataUrl("data:text/plain;base64," + Base64.getEncoder().encodeToString(ByteArray(200)), max = 100))
        assertNotNull(PageFiles.parseDataUrl("data:text/plain," + "x".repeat(100), max = 100))
    }

    @Test fun filesAreNamedByTheLinkThenTheServerThenTheirType() {
        assertEquals("chat-export.html", PageFiles.fileName("chat-export.html", null, "blob:https://e.com/1", "text/html"))
        assertEquals("passwd", PageFiles.fileName("../../etc/passwd", null, "blob:https://e.com/1", "text/plain"))
        assertEquals("report.pdf", PageFiles.fileName(null, "attachment; filename=\"report.pdf\"", "blob:https://e.com/1", "application/pdf"))
        assertEquals("résumé final.txt", PageFiles.fileName(null, "attachment; filename=\"fallback.txt\"; filename*=UTF-8''r%C3%A9sum%C3%A9%20final.txt", "https://e.com/x", null))
        assertEquals("plain name.txt", PageFiles.fileName(null, "attachment; filename=plain name.txt", "https://e.com/x", null))
        assertEquals("notes v2.md", PageFiles.fileName(null, null, "https://e.com/files/notes%20v2.md?x=1#y", null))
        assertEquals("download.html", PageFiles.fileName(null, null, "blob:https://e.com/1", "text/html; charset=utf-8"))
        assertEquals("download.html", PageFiles.fileName("", "", "data:text/html,x", "text/html"))
        assertEquals("download", PageFiles.fileName(null, null, "blob:https://e.com/1", "application/x-unknown"))
        assertEquals("download", PageFiles.fileName(null, null, "", null))
    }

    @Test fun thePagesReportIsCheckedBeforeAnythingIsKept() {
        val encoded = Base64.getEncoder().encodeToString(html.toByteArray())
        val ready = PageFiles.ready("ready", "text/html", html.length.toLong(), "chat.html", encoded.length.toLong())!!
        assertEquals("chat.html", ready.name)
        assertEquals(html, PageFiles.decode(ready, encoded.chunked(7))!!.toString(Charsets.UTF_8))
        assertNull(PageFiles.decode(ready, encoded.chunked(7).dropLast(1)))
        // A length that doesn't match the size, a size over the limit, or a job that isn't ready.
        assertNull(PageFiles.ready("ready", "text/html", html.length.toLong(), "", encoded.length + 4L))
        assertNull(PageFiles.ready("ready", "", PageFiles.MAX_BYTES + 1L, "", PageFiles.encodedLength(PageFiles.MAX_BYTES + 1L)))
        assertNull(PageFiles.ready("reading", "", 3, "", 4))
        assertNull(PageFiles.ready("ready", "", -1, "", 0))
        // Chunks of the right length that decode to a different size are refused.
        val short = PageFiles.ready("ready", "", 3, "", 4)!!
        assertNull(PageFiles.decode(short, listOf("QQ==")))
        assertEquals(0, PageFiles.encodedLength(0))
        assertEquals(4, PageFiles.encodedLength(1))
        assertEquals(4, PageFiles.encodedLength(3))
        assertEquals(8, PageFiles.encodedLength(4))
    }

    @Test fun scriptsQuoteThePagesAddressesAndTokens() {
        val url = "blob:https://e.com/1'\"</script>\\"
        val read = PageFiles.readScript("t-1", url)
        assertTrue(read.contains(org.json.JSONObject.quote(url)))
        assertFalse(read.contains("'\"</script>"))
        assertTrue(PageFiles.pollScript("t-1").contains("\"t-1\""))
        assertTrue(PageFiles.chunkScript("t-1", 1024, 512).contains("substr(1024,512)"))
        assertTrue(PageFiles.nameScript("data:" + "x".repeat(1000)).length < 800)
        assertFalse(PageFiles.START_SCRIPT.contains("\n"))
        assertTrue(PageFiles.START_SCRIPT.contains("URL.createObjectURL=function"))
        // The page's own revoke is left alone: kept blobs simply outlive it.
        assertFalse(PageFiles.START_SCRIPT.contains("revokeObjectURL"))
    }
}
