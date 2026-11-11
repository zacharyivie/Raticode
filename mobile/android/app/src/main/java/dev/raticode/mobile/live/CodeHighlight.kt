package dev.raticode.mobile.live

/**
 * A small code tokenizer for chat code blocks: comments, strings, numbers and keywords.
 * It never runs code. The desktop applies the same rules (frontend/src/lib/highlight.js).
 */
object CodeHighlight {
    enum class Kind { TEXT, COMMENT, STRING, NUMBER, KEYWORD, TAG }
    data class Token(val kind: Kind, val text: String)

    private const val C_LIKE = "break case catch class const continue default do else enum export extends false finally for function if import in instanceof interface let new null return static super switch this throw true try typeof var void while yield async await of"
    private val KEYWORDS = mapOf(
        "js" to C_LIKE,
        "ts" to "$C_LIKE type implements private public protected readonly namespace declare as",
        "py" to "and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield self",
        "kt" to "as break class continue do else false for fun if in interface is null object package return super this throw true try typealias val var when while data sealed internal private public override companion import",
        "java" to "abstract boolean break byte case catch char class continue default do double else enum extends final finally float for if implements import instanceof int interface long new null package private protected public return short static super switch this throw true false try void while",
        "go" to "break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var nil true false",
        "rust" to "as break const continue crate else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while async await dyn",
        "sh" to "if then else elif fi for while do done case esac in function return export local echo cd exit set unset",
        "sql" to "select from where and or not insert into values update set delete create table drop alter join left right inner outer on group by order limit as distinct null is in like having union",
        "c" to "auto break case char const continue default do double else enum extern float for goto if inline int long register return short signed sizeof static struct switch typedef union unsigned void volatile while bool true false nullptr class namespace template typename public private protected virtual override new delete this using try catch throw include define ifdef ifndef endif",
        "cs" to "abstract as base bool break byte case catch char class const continue decimal default delegate do double else enum event explicit extern false finally float for foreach if implicit in int interface internal is lock long namespace new null object operator out override params private protected public readonly ref return sealed short static string struct switch this throw true try typeof uint ulong using var virtual void while async await record get set",
        "rb" to "alias and begin break case class def defined do else elsif end ensure false for if in module next nil not or redo rescue retry return self super then true undef unless until when while yield require attr_accessor attr_reader",
        "php" to "abstract and array as break case catch class clone const continue declare default do echo else elseif empty endif extends final finally fn for foreach function global if implements include interface isset list match namespace new null or print private protected public readonly require return static switch throw trait true false try use var while yield",
        "swift" to "as associatedtype break case catch class continue default defer do else enum extension fallthrough false for func guard if import in init inout internal is let nil operator private protocol public repeat rethrows return self static struct subscript super switch throw throws true try var where while async await",
        "css" to "",
        "json" to "true false null",
        "yaml" to "true false null yes no",
    ).mapValues { (_, words) -> words.split(' ').filter { it.isNotEmpty() }.toSet() }
    private val ALIASES = mapOf(
        "javascript" to "js", "jsx" to "js", "mjs" to "js", "cjs" to "js", "typescript" to "ts", "tsx" to "ts", "python" to "py",
        "kotlin" to "kt", "kts" to "kt", "golang" to "go", "rs" to "rust", "bash" to "sh", "shell" to "sh", "zsh" to "sh", "fish" to "sh",
        "console" to "sh", "yml" to "yaml", "xml" to "html", "toml" to "yaml", "ini" to "yaml",
        "cpp" to "c", "c++" to "c", "h" to "c", "csharp" to "cs", "ruby" to "rb",
    )
    private const val MAX = 20_000
    private val NUMBER = Regex("""^(?:0x[\da-fA-F]+|\d[\d_]*\.?\d*(?:[eE][+-]?\d+)?|\.\d+)""")
    private val WORD = Regex("""^[A-Za-z_$][\w$]*""")
    private val HTML = Regex("""(<!--[\s\S]*?-->)|(</?[A-Za-z][^>]*>)""")

    /** The language named by a fenced block's info string, such as `TypeScript` → `ts`. */
    fun language(info: String): String = info.trim().substringBefore(' ').lowercase().let { ALIASES[it] ?: it }

    fun supports(language: String) = language in KEYWORDS || language == "html"

    fun tokenize(code: String, language: String): List<Token> {
        if (!supports(language) || code.length > MAX) return listOf(Token(Kind.TEXT, code))
        if (language == "html") return html(code)
        val keywords = KEYWORDS.getValue(language)
        val hash = language in setOf("py", "sh", "yaml", "rb", "php")
        val slash = language !in setOf("py", "sh", "yaml", "json", "sql", "rb")
        val tokens = mutableListOf<Token>()
        fun push(kind: Kind, text: String) {
            val last = tokens.lastOrNull()
            if (last != null && kind == Kind.TEXT && last.kind == Kind.TEXT) tokens[tokens.lastIndex] = Token(Kind.TEXT, last.text + text)
            else tokens.add(Token(kind, text))
        }
        var at = 0
        while (at < code.length) {
            val rest = code.substring(at)
            val match: Pair<Kind, String>? = when {
                slash && rest.startsWith("//") -> Kind.COMMENT to rest.substringBefore('\n')
                slash && rest.startsWith("/*") -> Kind.COMMENT to rest.indexOf("*/", 2).let { if (it < 0) rest else rest.substring(0, it + 2) }
                hash && rest.startsWith("#") -> Kind.COMMENT to rest.substringBefore('\n')
                language == "sql" && rest.startsWith("--") -> Kind.COMMENT to rest.substringBefore('\n')
                language == "py" && (rest.startsWith("\"\"\"") || rest.startsWith("'''")) ->
                    Kind.STRING to rest.indexOf(rest.substring(0, 3), 3).let { if (it < 0) rest else rest.substring(0, it + 3) }
                rest[0] == '"' || rest[0] == '\'' || rest[0] == '`' -> Kind.STRING to stringAt(rest)
                rest[0].isDigit() || (rest[0] == '.' && rest.length > 1 && rest[1].isDigit()) -> NUMBER.find(rest)?.let { Kind.NUMBER to it.value }
                rest[0].isLetter() || rest[0] == '_' || rest[0] == '$' -> WORD.find(rest)?.value?.let { word ->
                    (if ((if (language == "sql") word.lowercase() else word) in keywords) Kind.KEYWORD else Kind.TEXT) to word
                }
                else -> null
            }
            if (match != null && match.second.isNotEmpty()) { push(match.first, match.second); at += match.second.length }
            else { push(Kind.TEXT, code[at].toString()); at += 1 }
        }
        return tokens
    }

    private fun stringAt(rest: String): String {
        val quote = rest[0]
        var index = 1
        while (index < rest.length) {
            when {
                rest[index] == '\\' -> index += 2
                rest[index] == quote -> return rest.substring(0, index + 1)
                rest[index] == '\n' && quote != '`' -> return rest.substring(0, index)
                else -> index += 1
            }
        }
        return rest
    }

    private fun html(code: String): List<Token> {
        val tokens = mutableListOf<Token>()
        var at = 0
        for (match in HTML.findAll(code)) {
            if (match.range.first > at) tokens.add(Token(Kind.TEXT, code.substring(at, match.range.first)))
            tokens.add(Token(if (match.groups[1] != null) Kind.COMMENT else Kind.TAG, match.value))
            at = match.range.last + 1
        }
        if (at < code.length) tokens.add(Token(Kind.TEXT, code.substring(at)))
        return tokens
    }
}
