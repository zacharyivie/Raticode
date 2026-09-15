# Rattish 1 lexical specification

## Conformance language

`Must` and `must not` state requirements. `Should` states a recommendation that a tool may
depart from for a documented reason. Examples are informative unless labeled invalid.

## Source encoding

A Rattish source file must contain UTF-8 text. A compiler must reject invalid UTF-8. The
compiler may accept a leading UTF-8 byte-order mark and must not treat it as source text.

The lexer normalizes CRLF and CR newlines to LF before tokenization. Source spans retain
enough information to report the original byte offsets and line and column positions.

## Physical and logical lines

A physical line ends at a normalized LF or at end of file. A logical line is a physical
line after comment removal, except inside a literal block or embedded JSON value.

Blank logical lines do not affect indentation. The parser may accept a final line without
an LF. The formatter emits one final LF.

## Indentation

`Radish` is accepted as a legacy alias for `Rattish` in the version directive only.
New files and canonical formatting use `Rattish` or `rattish`.

Rattish uses indentation to delimit blocks.

- One indentation level is two columns.
- A space advances one column.
- A tab in leading indentation advances two columns.
- A tab outside leading indentation is ordinary source content when its token permits it.
- A nonblank logical line must begin at an even indentation column.
- A new nested block must increase indentation by exactly one level.
- A dedent must return to a previously active indentation level.

The lexer emits `INDENT` after a line-ending token when the next nonblank logical line is
one level deeper. It emits one or more `DEDENT` tokens when indentation decreases. It emits
the remaining `DEDENT` tokens before `EOF`.

The formatter replaces leading tabs with two spaces and emits two spaces per level.

## Canonical formatting

The canonical formatter requires a grammatically valid document. It does not format a
recovering AST or write partial output after a parse error.

The formatter:

- emits lowercase Rattish keywords, field names, declaration IDs, references, and duration
  units;
- preserves the case and content of strings, paths, prompts, commands, JSON member names,
  and structured-output selectors;
- emits two spaces for each indentation level and one space after `:` and list markers;
- emits one blank line between top-level declarations and one final LF;
- emits strict JSON with sorted object keys and preserved JSON string content;
- preserves standalone comments, inline comments, and `#` text inside strings and literal
  blocks.

Formatting is idempotent. Formatting canonical source again must produce identical bytes.

## Case

Keywords, field names, declaration identifiers, and declaration references compare
case-insensitively. The compiler canonicalizes them to lowercase. The formatter emits
their lowercase spelling.

The compiler normalizes only tokens used in case-insensitive identifier roles. These
include keywords, field names, declaration names, node and workflow interface IDs,
built-in node types, provider IDs, and closed-set identifier values.

The compiler preserves string content, paths, URLs, commands, prompts, model names,
literal blocks, JSON object member names, JSON string values, and structured-output member
selectors. It never lowercases the source buffer. The concrete tree retains original token
spelling and the semantic AST stores a separate canonical value for normalized identifiers.

## Identifiers

An `IDENTIFIER` has this regular form:

```text
[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*
```

An identifier therefore:

- starts with an ASCII letter;
- contains only ASCII letters, digits, and single hyphens between segments;
- has no consecutive hyphens;
- has no trailing hyphen.

Identifiers that differ only by ASCII letter case are the same identifier. Two declarations
that canonicalize to the same identifier are a semantic error.

## Punctuation

The lexer recognizes these punctuation tokens outside strings and embedded JSON:

```text
:  -  .  (  )  [  ]  |  ==  !=  <  <=  >  >=
```

A colon terminates a declaration header, field name, or map key. A hyphen followed by
layout whitespace begins a block-list item. Other hyphens may occur inside identifiers.

## Comments

`#` begins a comment everywhere outside a double-quoted string, literal block string, or
embedded JSON value. The comment ends immediately before the physical newline.

```rattish
Node review: # declaration comment
  type: agent
```

A bare value containing `#` must use double quotes or a literal block. Embedded JSON uses
strict JSON and does not permit comments outside JSON strings.

## Reserved words

The following case-insensitive words have grammatical meaning in Rattish 1:

```text
Rattish Workflow Node needs to with from default when otherwise
and or not contains matches exists is null true false none
succeeded failed pass fail
```

Node types and field contracts may define more closed-set values without making them
language keywords.

## Integers and numbers

An integer contains an optional minus sign followed by one or more decimal digits. Leading
zeroes are forbidden except for `0` itself.

```text
INTEGER = -?(0|[1-9][0-9]*)
```

A number uses the JSON number form. It may contain a fractional part or base-10 exponent.
The lexer does not accept `NaN`, positive or negative infinity, a leading plus sign, or a
leading decimal point.

Field contracts decide whether a numeric token accepts only integers or any finite number.

## Booleans and absence

`true` and `false` are Boolean literals. `none` is the Rattish absence literal for optional
fields. `null` is the JSON null literal and predicate keyword. Field contracts decide
whether either form is legal at a given location.

## Durations

A duration is one positive integer immediately followed by one unit:

```text
DURATION = POSITIVE-INTEGER ("ms" | "s" | "m" | "h" | "d")
```

Units compare case-insensitively and the formatter emits lowercase. Rattish 1 does not
accept compound durations such as `1h30m`, decimal durations, or whitespace between the
number and unit.

## Double-quoted strings

A double-quoted string follows JSON string syntax. It supports JSON escapes and rejects
unescaped control characters and physical newlines. The decoded Unicode scalar sequence
is the string value.

```rattish
name: "Review PR"
prompt-path: "./prompts/review #1.md"
```

## Bare strings

A bare string occupies the remaining non-comment text on one logical line. The lexer trims
leading and trailing layout whitespace. A bare string cannot be empty.

The parser first recognizes reserved literals, identifiers, numbers, durations, routes,
references, and predicates when the field context calls for them. Otherwise it emits a
`BARE_STRING`. Field contracts decide whether that string is legal.

Authors must quote a value when they need leading or trailing whitespace, a `#`, or text
that would otherwise be read as another token kind.

## Literal block strings

`|` after a field colon begins a literal block. The content starts on the next physical
line. Every nonblank content line must be indented deeper than the field line. The block
ends before the first nonblank line at the field's indentation or less.

The lexer finds the smallest indentation shared by all nonblank content lines and removes
that many columns from every content line. It preserves further indentation and blank
lines. An empty block produces an empty string. A nonempty block produces exactly one
trailing LF, regardless of the source file's final newline.

Comments have no special meaning inside a literal block.

```rattish
Node review:
  type: agent
  prompt: |
    Review the change. # This text is part of the prompt.

    Return JSON.
  effort: high
```

The `effort` line ends the block because it returns to the indentation of `prompt`.

## Embedded JSON

When an inline value begins with `{` or `[`, the lexer enters JSON mode and consumes one
balanced strict JSON value. JSON mode observes JSON string escaping when matching brackets.
The JSON value may span physical lines. Its continuation indentation does not emit Rattish
`INDENT` or `DEDENT` tokens.

Rattish delegates the consumed text to a strict JSON parser. JSON comments, trailing commas,
duplicate object member names, invalid escapes, and non-finite numbers are errors. A field
contract may require an object, array, or narrower schema after parsing.

JSON strings, numbers, Booleans, and null also use the ordinary Rattish scalar tokens when
they do not begin with `{` or `[`. This still permits every JSON value without adding a
second scalar syntax.

## Newline and end tokens

Outside literal blocks and embedded JSON, each nonblank logical line ends with `NEWLINE`.
The lexer emits `EOF` after the last logical line and any required `DEDENT` tokens.

## Editor recovery

The strict compiler parser stops without an AST when lexical or grammatical errors remain.
Editor tooling may use the same lexer in recovering mode. A recovering parse retains the
shared token stream, including comment trivia, skips an invalid Node declaration, and keeps
later declarations that parse independently. A recovering AST is never executable input and
cannot be lowered to IR while any error diagnostic remains.
