package dev.raticode.mobile.voice

import org.junit.Assert.assertEquals
import org.junit.Test

/** The phone's own text tidying: no model, so the same words always give the same message. */
class TextTidyTest {
    @Test fun capitalizesAndEndsSentences() {
        assertEquals("I'm running late.", TextTidy.tidy("i'm running late"))
        assertEquals("You up?", TextTidy.tidy("you up"))
        assertEquals("Are you coming to dinner?", TextTidy.tidy("are you coming to dinner"))
        assertEquals("On my way. Be there in 10.", TextTidy.tidy("on my way. be there in 10"))
        assertEquals("I think I left it at home.", TextTidy.tidy("i think i left it at home"))
    }

    @Test fun dropsHesitationsAndStumblesOnly() {
        assertEquals("I think we should go.", TextTidy.tidy("um I I think uh we should go"))
        assertEquals("The plan is fine.", TextTidy.tidy("the the plan is fine"))
        assertEquals("It works.", TextTidy.tidy("it it works"))
        // Repeats said on purpose stay.
        assertEquals("No no no, that's very very wrong.", TextTidy.tidy("no no no, that's very very wrong"))
    }

    @Test fun spokenPunctuation() {
        assertEquals("Hey, are you free?", TextTidy.tidy("hey comma are you free question mark"))
        assertEquals("We won!", TextTidy.tidy("we won exclamation point"))
        assertEquals("See you soon.", TextTidy.tidy("see you soon period"))
        // "period" in the middle is a word, not a full stop.
        assertEquals("My period tracker app is broken.", TextTidy.tidy("my period tracker app is broken"))
        assertEquals("First line\nSecond line.", TextTidy.tidy("first line new line second line"))
    }

    @Test fun profanityStaysAndMaskedWordsAreWrittenOut() {
        assertEquals("This is fucking great.", TextTidy.tidy("this is fucking great"))
        assertEquals("Holy shit, you did it!", TextTidy.tidy("holy shit, you did it!"))
        assertEquals("What the fuck?", TextTidy.tidy("what the f***"))
        assertEquals("Fuck yes.", TextTidy.tidy("F*** yes"))
        // Masks with more than one likely word are left as they are.
        assertEquals("Don't be a d***.", TextTidy.tidy("don't be a d***"))
    }

    @Test fun keepsWhatIsAlreadyFine() {
        assertEquals("Running late 🙃", TextTidy.tidy("Running late 🙃"))
        assertEquals("Meet at 5:30?", TextTidy.tidy("meet at 5:30?"))
        assertEquals("", TextTidy.tidy("   um  "))
    }
}
