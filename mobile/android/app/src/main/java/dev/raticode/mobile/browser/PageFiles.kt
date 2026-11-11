package dev.raticode.mobile.browser

import org.json.JSONObject
import java.util.Base64

/**
 * Files a page builds itself, such as a chat Open WebUI exports: a `blob:` or `data:` link with a download attribute.
 * Android's download manager fetches only http and https, so these are read in the page instead.
 *
 * The phone's WebView has no JavaScript bridge to the app. [START_SCRIPT] runs before each page's own scripts and keeps
 * the last few blobs the page turned into addresses, for one minute, so a page that revokes its address right after the
 * click (as Open WebUI does) can still be saved. [readScript] has the page encode the file, and the app collects it by
 * asking the page in [chunkScript] pieces. Everything read comes from the page that offered the file, and the user still
 * confirms before anything is saved. Kept apart from Android so plain JUnit can test it.
 */
object PageFiles {
    /** The largest file a page may hand over this way. */
    const val MAX_BYTES = 25 * 1024 * 1024
    /** Base64 characters per read; evaluateJavascript returns each as one string. */
    const val CHUNK = 512 * 1024
    /** How long the page may take to encode a file before the save is given up. */
    const val READ_TIMEOUT_MS = 20_000L
    private const val KEEP_MS = 60_000
    private const val KEEP = 20
    private const val NAME_KEY = 512
    /** Lowercase, so the script never collides with a page's own names. */
    private const val STORE = "__raticodePageFiles"

    fun isPageFile(url: String) = url.startsWith("blob:", ignoreCase = true) || url.startsWith("data:", ignoreCase = true)

    /** A secure page's own file: a blob that page's https origin made, or a data: link on an https page. */
    fun allowed(url: String, pageUrl: String): Boolean = when {
        url.startsWith("blob:", ignoreCase = true) -> BrowserRules.pageAddress(url.substring(5)) && url.substring(5).startsWith("https://", ignoreCase = true)
        url.startsWith("data:", ignoreCase = true) -> BrowserRules.pageAddress(pageUrl) && pageUrl.startsWith("https://", ignoreCase = true)
        else -> false
    }

    /**
     * Whether a Gecko tab may follow a page's link to its own file. Gecko asks before every link, downloads included,
     * and cannot say which links are downloads, so this allows a blob: the triggering page's own origin made, and a
     * data: link from a web page. Gecko itself still refuses to show a data: page a site sends the tab to.
     */
    fun geckoMayLoad(uri: String, trigger: String?): Boolean {
        val page = trigger?.takeIf { BrowserRules.pageAddress(it) || it.startsWith("http://", ignoreCase = true) } ?: return false
        return when {
            uri.startsWith("blob:", ignoreCase = true) -> origin(uri.substring(5))?.let { it == origin(page) } == true
            uri.startsWith("data:", ignoreCase = true) -> true
            else -> false
        }
    }

    /** scheme://host:port of an http or https address, or null. */
    private fun origin(url: String): String? = runCatching {
        val parsed = java.net.URI(url)
        val scheme = parsed.scheme?.lowercase()?.takeIf { it == "https" || it == "http" } ?: return null
        val host = parsed.host?.lowercase()?.takeIf { it.isNotEmpty() } ?: return null
        val port = if (parsed.port >= 0) parsed.port else if (scheme == "https") 443 else 80
        "$scheme://$host:$port"
    }.getOrNull()

    /** The site to name in the question: the blob's own origin, or the page showing a data: link. */
    fun host(url: String, pageUrl: String): String =
        if (url.startsWith("blob:", ignoreCase = true)) BrowserRules.host(url.substring(5)) else BrowserRules.host(pageUrl)

    // data: ---------------------------------------------------------------------------------------------------------

    class DataFile(val mime: String, val bytes: ByteArray)

    /** Decodes a data: address, or null when it is malformed or larger than [max] bytes. */
    fun parseDataUrl(url: String, max: Int = MAX_BYTES): DataFile? {
        if (!url.startsWith("data:", ignoreCase = true)) return null
        val comma = url.indexOf(',')
        if (comma < 0) return null
        val params = url.substring(5, comma).split(';').map { it.trim() }
        val mime = params.first().lowercase().takeIf { Regex("^[a-z0-9.+-]+/[a-z0-9.+-]+$").matches(it) } ?: "text/plain"
        val payload = url.substring(comma + 1)
        val bytes = if (params.drop(1).any { it.equals("base64", ignoreCase = true) }) {
            val text = percentDecode(payload, max * 2L)?.toString(Charsets.US_ASCII)?.filterNot { it.isWhitespace() } ?: return null
            if (text.length.toLong() * 3 / 4 > max + 3L) return null
            runCatching { Base64.getDecoder().decode(text) }.getOrNull() ?: return null
        } else {
            percentDecode(payload, max.toLong()) ?: return null
        }
        return if (bytes.size > max) null else DataFile(mime, bytes)
    }

    /** %XX escapes to bytes and other characters as UTF-8; null when it would pass [max] bytes or an escape is broken. */
    private fun percentDecode(text: String, max: Long): ByteArray? {
        val out = java.io.ByteArrayOutputStream(minOf(text.length.toLong(), max).toInt())
        var i = 0
        while (i < text.length) {
            val c = text[i]
            if (c == '%') {
                if (i + 2 >= text.length) return null
                val value = text.substring(i + 1, i + 3).toIntOrNull(16) ?: return null
                out.write(value)
                i += 3
            } else {
                out.write(c.toString().toByteArray(Charsets.UTF_8))
                i += 1
            }
            if (out.size() > max) return null
        }
        return out.toByteArray()
    }

    // Names ---------------------------------------------------------------------------------------------------------

    private val EXTENSIONS = mapOf(
        "text/html" to "html", "text/plain" to "txt", "text/markdown" to "md", "text/csv" to "csv", "text/css" to "css",
        "application/json" to "json", "application/pdf" to "pdf", "application/zip" to "zip", "application/xml" to "xml",
        "text/xml" to "xml", "image/png" to "png", "image/jpeg" to "jpg", "image/gif" to "gif", "image/webp" to "webp",
        "image/svg+xml" to "svg", "audio/mpeg" to "mp3", "audio/wav" to "wav", "video/mp4" to "mp4", "video/webm" to "webm",
    )

    /** The name a Content-Disposition header gives, preferring the RFC 5987 `filename*` form. */
    fun dispositionName(disposition: String?): String? {
        val value = disposition.orEmpty()
        Regex("filename\\*\\s*=\\s*([^']*)'[^']*'([^;]+)", RegexOption.IGNORE_CASE).find(value)?.let { match ->
            val charset = runCatching { charset(match.groupValues[1].ifBlank { "UTF-8" }) }.getOrDefault(Charsets.UTF_8)
            percentDecode(match.groupValues[2].trim().trim('"'), 4096)?.toString(charset)?.takeIf { it.isNotBlank() }?.let { return it }
        }
        return Regex("filename\\s*=\\s*(?:\"([^\"]*)\"|([^;]+))", RegexOption.IGNORE_CASE).find(value)
            ?.let { (it.groupValues[1].ifEmpty { it.groupValues[2] }).trim() }?.takeIf { it.isNotBlank() }
    }

    /**
     * What to call a downloaded file: the link's own download name, then the server's, then the address's last part
     * for web addresses, then "download" with an extension for its type.
     */
    fun fileName(suggested: String?, disposition: String?, url: String, mime: String?): String {
        val type = mime.orEmpty().substringBefore(';').trim().lowercase()
        val fromUrl = if (BrowserRules.pageAddress(url)) {
            url.substringBefore('#').substringBefore('?').substringAfterLast('/').let { part ->
                runCatching { java.net.URLDecoder.decode(part.replace("+", "%2B"), "UTF-8") }.getOrDefault(part)
            }.takeIf { it.isNotBlank() }
        } else null
        val chosen = suggested?.takeIf { it.isNotBlank() } ?: dispositionName(disposition) ?: fromUrl
        if (chosen != null) return BrowserRules.safeName(chosen)
        return EXTENSIONS[type]?.let { "download.$it" } ?: "download"
    }

    // The page's side -----------------------------------------------------------------------------------------------

    /** Runs before each page's scripts in every frame (WebViewCompat.addDocumentStartJavaScript). */
    val START_SCRIPT = """
        (function(){try{if(window.$STORE)return;var blobs=new Map(),names=new Map(),create=URL.createObjectURL;
        Object.defineProperty(window,'$STORE',{value:{blobs:blobs,names:names,jobs:{}}});
        function keep(m,k,v){m.set(k,v);if(m.size>$KEEP)m.delete(m.keys().next().value);setTimeout(function(){if(m.get(k)===v)m.delete(k)},$KEEP_MS)}
        URL.createObjectURL=function(o){var u=create.apply(URL,arguments);try{if(o instanceof Blob&&o.size<=$MAX_BYTES)keep(blobs,u,o)}catch(e){}return u};
        addEventListener('click',function(e){try{var p=e.composedPath();for(var i=0;i<p.length;i++){var a=p[i];
        if(a&&a.tagName==='A'&&a.hasAttribute('download')&&/^(blob|data):/i.test(a.href)){keep(names,String(a.href).slice(0,$NAME_KEY),a.getAttribute('download'));break}}}catch(e){}},true)
        }catch(e){}})();
    """.trimIndent().replace("\n", "")

    private fun store() = "(window.$STORE||(window.$STORE={blobs:new Map(),names:new Map(),jobs:{}}))"

    /** The download name the page's link gave [url], as a JSON string. */
    fun nameScript(url: String) = "(function(){var s=${store()};return s.names.get(${JSONObject.quote(url.take(NAME_KEY))})||''})()"

    /** Starts encoding [url]'s file in the page under [token]; returns "started". */
    fun readScript(token: String, url: String): String {
        val t = JSONObject.quote(token)
        val u = JSONObject.quote(url)
        return "(function(){var s=${store()},j={state:'reading'};s.jobs[$t]=j;j.name=s.names.get($u.slice(0,$NAME_KEY))||'';" +
            "var b=s.blobs.get($u);(b?Promise.resolve(b):fetch($u).then(function(r){return r.blob()})).then(function(b){" +
            "if(b.size>$MAX_BYTES){j.state='big';return}j.type=b.type;j.size=b.size;var f=new FileReader();" +
            "f.onload=function(){var d=String(f.result);j.data=d.slice(d.indexOf(',')+1);j.state='ready'};" +
            "f.onerror=function(){j.state='failed'};f.readAsDataURL(b)}).catch(function(){j.state='failed'});return 'started'})()"
    }

    /** The job's state as an object: state, type, size, name and the encoded length. */
    fun pollScript(token: String) = "(function(){var s=window.$STORE,j=s&&s.jobs[${JSONObject.quote(token)}];if(!j)return null;" +
        "return {state:String(j.state),type:String(j.type||''),size:Number(j.size)||0,name:String(j.name||''),length:j.data?j.data.length:0}})()"

    fun chunkScript(token: String, from: Int, length: Int) =
        "(function(){var s=window.$STORE,j=s&&s.jobs[${JSONObject.quote(token)}];return j&&j.data?String(j.data.substr($from,$length)):null})()"

    fun forgetScript(token: String) = "(function(){var s=window.$STORE;if(s)delete s.jobs[${JSONObject.quote(token)}];return true})()"

    /** The page's report, checked: a ready job with a believable size and encoded length, else null. */
    data class Ready(val type: String, val size: Int, val name: String, val length: Int)

    fun ready(state: String, type: String, size: Long, name: String, length: Long): Ready? {
        if (state != "ready" || size < 0 || size > MAX_BYTES) return null
        if (length != encodedLength(size)) return null
        return Ready(type.take(200), size.toInt(), name.take(1000), length.toInt())
    }

    fun encodedLength(size: Long) = (size + 2) / 3 * 4

    /** Joins the chunks the page sent and decodes them, or null when they don't add up to [ready]. */
    fun decode(ready: Ready, chunks: List<String>): ByteArray? {
        val text = chunks.joinToString("")
        if (text.length != ready.length) return null
        val bytes = runCatching { Base64.getDecoder().decode(text) }.getOrNull() ?: return null
        return bytes.takeIf { it.size == ready.size }
    }
}
