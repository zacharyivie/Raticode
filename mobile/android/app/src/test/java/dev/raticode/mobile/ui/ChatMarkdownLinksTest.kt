package dev.raticode.mobile.ui

import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.font.FontFamily
import dev.raticode.mobile.live.ChatLink
import dev.raticode.mobile.live.LinkSource
import org.commonmark.parser.Parser
import org.junit.Assert.*
import org.junit.Test

/** Message links render as links, keep their label, and tell the screen exactly what was tapped. */
class ChatMarkdownLinksTest {
    private val source = LinkSource.of("rem", null, "m1")!!
    private val tapped = mutableListOf<Pair<ChatLink, LinkSource?>>()
    private val handler = ChatLinkHandler { link, from -> tapped.add(link to from) }

    private fun render(markdown: String, links: LinkContext = LinkContext(handler, source, TextLinkStyles())): AnnotatedString =
        inlineMarkdown(Parser.builder().build().parse(markdown).firstChild, links)

    private fun AnnotatedString.links() = getLinkAnnotations(0, length).map { text.substring(it.start, it.end) to it.item }

    private fun tap(text: AnnotatedString, label: String) {
        val link = text.links().first { it.first == label }.second as LinkAnnotation.Clickable
        link.linkInteractionListener!!.onClick(link)
    }

    @Test fun fileLinksWithSpacesKeepTheirLabelAndAskForTheExactLink() {
        val text = render("The [cited plan](</home/doonk/Projects/second-brain/reports/a b.html>) recommends.")
        assertEquals("The cited plan recommends.", text.text)
        assertEquals(listOf("cited plan"), text.links().map { it.first })
        tap(text, "cited plan")
        assertEquals(listOf(ChatLink.File("/home/doonk/Projects/second-brain/reports/a b.html", "a b.html") to source), tapped)
    }

    @Test fun webLinksAndPlainAddressesOpenTheBrowser() {
        val text = render("Read [the docs](https://example.com/docs) or https://example.org/x.")
        assertEquals("Read the docs or https://example.org/x.", text.text)
        assertEquals(listOf("the docs", "https://example.org/x"), text.links().map { it.first })
        tap(text, "the docs"); tap(text, "https://example.org/x")
        assertEquals(listOf(ChatLink.Web("https://example.com/docs"), ChatLink.Web("https://example.org/x")), tapped.map { it.first })
    }

    @Test fun plainPathsAndAutolinksAreLinked() {
        val text = render("Swarms: /home/doonk/reports/x.html and <https://example.com>")
        assertEquals(listOf("/home/doonk/reports/x.html", "https://example.com"), text.links().map { it.first })
    }

    @Test fun unsafeSchemesCodeAndAnchorsStayText() {
        val text = render("[run](javascript:alert(1)) [mail](mailto:a@example.com) [top](#top) `https://example.com/in-code`")
        assertEquals("run mail top https://example.com/in-code", text.text)
        // Only the web address links, even written as code; the other schemes and the anchor stay text.
        assertEquals(listOf("https://example.com/in-code"), text.links().map { it.first })
        assertTrue(render("`javascript:alert(1)` `intent://x` `mailto:a@example.com`").links().isEmpty())
    }

    @Test fun fileLinksOutsideAMessageAreShownButNotFetched() {
        val text = render("Open [a.md](notes/a.md) or [site](https://example.com)", LinkContext(handler, null, TextLinkStyles()))
        assertEquals(listOf("site"), text.links().map { it.first })
        assertTrue(text.spanStyles.any { text.text.substring(it.start, it.end) == "a.md" && it.item.fontFamily == FontFamily.Monospace })
    }

    @Test fun withoutAHandlerWebLinksUseTheDefaultOpener() {
        val text = render("[site](https://example.com) and [a.md](a.md)", LinkContext(null, source, TextLinkStyles()))
        assertEquals(listOf("site" to LinkAnnotation.Url("https://example.com", TextLinkStyles())), text.links())
    }

    @Test fun emptyLabelsShowTheAddress() {
        assertEquals("https://example.com", render("[](https://example.com)").text)
    }

    // Texts as Rem wrote them in the user's chat on 2026-11-02 and 2026-11-05.
    private val demo = "/home/doonk/Projects/Raticode/Raticode/.worktrees/organizations-ux-demo/docs/features/organizations-demo/index.html"
    private val shot = "/home/doonk/Projects/Raticode Mobile/screenshots/voice-synth/small-320dp-paused.png"

    private fun mentioned(vararg paths: String) = LinkContext(handler, source, TextLinkStyles(), dev.raticode.mobile.live.ChatLinks.mentions(paths.toList()))

    @Test fun pathsTheDesktopFoundLinkInPlainTextAndCodeIncludingSpaces() {
        val text = render("The earlier path was wrong; open `$demo` in a browser. The paused screen is $shot, and ~/notes/plan.md too.",
            mentioned(demo, shot, "~/notes/plan.md"))
        assertEquals(listOf(demo, shot, "~/notes/plan.md"), text.links().map { it.first })
        // The code span keeps its code look inside the link.
        assertTrue(text.spanStyles.any { text.text.substring(it.start, it.end) == demo && it.item.fontFamily == FontFamily.Monospace })
        tap(text, shot); tap(text, "~/notes/plan.md")
        assertEquals(listOf(ChatLink.File(shot, "small-320dp-paused.png"), ChatLink.File("~/notes/plan.md", "plan.md")), tapped.map { it.first })
    }

    @Test fun withoutTheDesktopsListOnlyPathsWithoutSpacesLink() {
        // An older desktop sends no mentions: the phone still links what it can tell is a path on its own.
        val text = render("Open `$demo` or $shot.")
        assertEquals(listOf(demo), text.links().map { it.first })
    }

    @Test fun codeThatIsExactlyAnAddressLinksAndOtherCodeStaysText() {
        val text = render("Run `npm test` in `src/app.py`, then open `file:///home/doonk/demo/index.html` or `https://example.com/x`.")
        assertEquals(listOf("file:///home/doonk/demo/index.html", "https://example.com/x"), text.links().map { it.first })
        tap(text, "file:///home/doonk/demo/index.html")
        assertEquals(ChatLink.File("file:///home/doonk/demo/index.html", "index.html"), tapped.single().first)
    }

    @Test fun aMentionLinksOnlyWhereAPathCanStart() {
        // "/a/b.txt" inside a longer path or a web address is not a separate mention.
        val text = render("See /x/a/b.txt and https://example.com/a/b.txt and (/a/b.txt).", mentioned("/a/b.txt"))
        // "/x/a/b.txt" still links on its own as a path; the mention links only inside the brackets.
        assertEquals(listOf("/x/a/b.txt", "https://example.com/a/b.txt", "/a/b.txt"), text.links().map { it.first })
        tap(text, "/a/b.txt")
        assertEquals(ChatLink.File("/a/b.txt", "b.txt"), tapped.single().first)
    }

    @Test fun markdownLinksStillWinOverMentions() {
        val text = render("[the paused screen](<$shot>) and $shot", mentioned(shot))
        assertEquals(listOf("the paused screen", shot), text.links().map { it.first })
    }
}
