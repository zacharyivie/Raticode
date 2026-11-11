package dev.raticode.mobile.live

import org.junit.Assert.*
import org.junit.Test

/** What Ask Rem puts in Rem's message box, and what it refuses to. */
class AskRemTest {
    private val hunk = DiffHunk("@@ -10,2 +10,3 @@ fun main()", listOf(
        DiffLine(' ', "keep", 10), DiffLine('-', "old", 11), DiffLine('+', "new", 11), DiffLine('+', "more", 12), DiffLine('\\', "No newline at end of file", null)))

    @Test fun aFileSelectionNamesTheProjectPathLinesAndText() {
        assertEquals("""
            About this file: app/src/Main.kt
            Project: Raticode Mobile
            Lines: 12–18

            ```
            val a = 1
            ```
        """.trimIndent(), AskRem.file("Raticode Mobile", "app/src/Main.kt", 12..18, "val a = 1"))
        assertEquals("About this file: a.kt\nProject: P\nLines: 7\n\n```\nx\n```", AskRem.file("P", "a.kt", 7..7, "x"))
    }

    @Test fun anUnknownLineRangeIsLeftOutRatherThanGuessed() {
        val words = AskRem.file("P", "notes.md", null, "A rendered sentence.")!!
        assertFalse(words.contains("Lines:"))
        assertTrue(words.endsWith("```\nA rendered sentence.\n```"))
    }

    @Test fun aWholeFileChangeSaysWhereTheChangeIs() {
        assertEquals("About this change: a.kt\nProject: P\nChange: staged", AskRem.diffFile("P", "a.kt", "staged", ""))
        assertEquals("About this change: a.kt\nProject: P\nChange: not staged", AskRem.diffFile("P", "a.kt", "unstaged", ""))
        assertEquals("About this change: a.kt\nProject: P\nChange: in commit abc123", AskRem.diffFile("P", "a.kt", "commit", "abc123"))
    }

    @Test fun aHunkCarriesItsLinesAndItsChanges() {
        val words = AskRem.hunk("P", "a.kt", "commit", "abc123", hunk)!!
        assertEquals("""
            About this change: a.kt
            Project: P
            Change: in commit abc123
            Lines: 10–12

            ```diff
            @@ -10,2 +10,3 @@ fun main()
             keep
            -old
            +new
            +more
            \ No newline at end of file
            ```
        """.trimIndent(), words)
    }

    @Test fun hunkLineRangesFollowTheNewFileUnlessLinesWereOnlyRemoved() {
        assertEquals("Lines: 5–7", AskRem.hunkLines("@@ -5 +5,3 @@"))
        assertEquals("Lines: 8", AskRem.hunkLines("@@ -7,2 +8 @@"))
        assertEquals("Lines: 1–4", AskRem.hunkLines("@@ -0,0 +1,4 @@"))
        assertEquals("Lines: 3–5 of the earlier version", AskRem.hunkLines("@@ -3,3 +2,0 @@"))
        assertNull(AskRem.hunkLines("not a hunk"))
    }

    @Test fun aCommitIsItsProjectAndFullId() {
        val hash = "a".repeat(40)
        assertEquals("About this commit: $hash\nProject: P", AskRem.commit("P", hash))
        assertEquals("About this commit: $hash", AskRem.commit("", hash))
    }

    @Test fun secretLookingFilesGetNoWordsAndNoPath() {
        // Each alternative of the desktop's rule, in the desktop's own words where it has them.
        for (path in listOf(".env", "config/.env.local", "auth.json", "id_rsa", "ssh/id_ed25519.pub", "server.key", "cert.PEM", "x.jks", "app.keystore",
                "db_password.txt", "API_KEY.txt", "api-key", "oauth_client.json", "my-token", "credentials.yml", "app-secrets.yaml", ".netrc", "_netrc",
                ".npmrc", ".pypirc", ".pgpass", ".dockercfg", "kubeconfig", "infra/prod.tfstate", "infra/prod.tfstate.backup", ".git/config", ".docker/config.json", ".kube/config")) {
            assertTrue(path, AskRem.refused(path))
            assertNull(path, AskRem.file("P", path, 1..2, "text"))
            assertNull(path, AskRem.diffFile("P", path, "unstaged", ""))
            assertNull(path, AskRem.hunk("P", path, "unstaged", "", hunk))
        }
    }

    @Test fun ordinaryFilesAreNotRefused() {
        for (path in listOf("README.md", "src/main/App.kt", "config/app.yaml", "keyboard.kt", "environment.ts", "config", "git/config", "docs/identity.md"))
            assertFalse(path, AskRem.refused(path))
    }

    @Test fun longTextIsShortenedAndSaysSo() {
        val words = AskRem.file("P", "a.kt", null, "x".repeat(AskRem.MAX_QUOTE + 500))!!
        assertTrue(words.contains("x".repeat(AskRem.MAX_QUOTE) + "\n```"))
        assertFalse(words.contains("x".repeat(AskRem.MAX_QUOTE + 1)))
        assertTrue(words.endsWith("(Shortened to the first 2,000 characters.)"))
        // The whole message fits the message box, and a character is never cut in half.
        assertTrue(words.length < 4000)
        val emoji = AskRem.file("P", "a.kt", null, "😀".repeat(AskRem.MAX_QUOTE))!!
        assertFalse(emoji.substringAfter("```\n").substringBefore("\n```").last().isHighSurrogate())
    }

    @Test fun selectedTextCannotBreakOutOfItsBlockOrAddLines() {
        val words = AskRem.file("P", "a.md", null, "before\n```\nafter")!!
        assertTrue(words.contains("\n\n````\nbefore\n```\nafter\n````"))
        val named = AskRem.file("My\nProject", "odd\nname.txt", null, "x")!!
        assertTrue(named.startsWith("About this file: odd name.txt\nProject: My Project\n\n"))
        assertFalse(AskRem.file("P", "a.kt", null, "   ")!!.contains("```"))
    }
}
