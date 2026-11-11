package dev.raticode.mobile.data

import java.security.MessageDigest

object FileMetadata {
        const val MAX_FILE = 10L * 1024 * 1024
        const val MAX_TOTAL = 50L * 1024 * 1024
        fun safeName(value: String): String = value.substringAfterLast('/').substringAfterLast('\\')
            .filterNot { it.isISOControl() || it in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069" }.trim().take(160).takeUnless { it.isEmpty() || it == "." || it == ".." } ?: "attachment"
        fun digest(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
}
