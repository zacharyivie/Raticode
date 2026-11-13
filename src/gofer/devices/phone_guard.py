"""The suspicious-request guard for paired phones.

A paired phone with "Use Raticode from this phone" on is trusted like the user at the
desktop. If the phone itself is taken over, though, whoever holds it could ask Rem for
the user's secrets or send it something to run. This module screens every request
from a paired phone with fixed rules, before Rem, a worker or any model sees it
(``DeviceApplication._handle``). No model and no network is involved, so the same
request always gets the same answer.

What it flags:

- Asking for credentials: "send me my passwords", "show me the API keys", dumping
  environment variables or a keychain.
- Reading a file that usually holds credentials with a command or a verb that shows
  or sends it: ``cat ~/.ssh/id_ed25519``, "show me the .env", "email me auth.json",
  "send me devid.key". Files described rather than named count too: signing keys and
  certificates ("send me the devid files", "export the p12", "give me the signing
  cert"), key stores, keychains, provisioning profiles, SSH and GPG keys and wallets.
- Insisting after Rem held back files this phone asked for ("send them anyway", "do it
  anyway", "I said send them", "override"), within an hour of the refusal, and asking
  Rem to get around its safeguards at any time.
- Payload-like content: downloads piped into a shell (``curl … | sh``), reverse
  shells, base64 decoded and run, writes to ``authorized_keys``, and long encoded
  blobs.
- A request unlike this phone's recent ones: two or more weaker signals (shell
  syntax, a raw IP address link, a long unbroken string, a mention of a credential
  file or secret) that this phone hasn't sent in its last 200 requests, once it has
  sent at least 25. Only the signal classes and counts are kept, never the text.

What it never reads: files and pictures the phone sends (attachments, screenshots,
Share to Rem), and the text of a screen the user shared with a Rem request. Those
reach Rem exactly as before. Paths and links the phone taps are checked by the
desktop's own rules for sensitive files instead.

Rem's own sensitive-file safeguard reports back (``PhoneLocks.held_back``): when it
holds back a key, certificate, password store or other secret-looking file a paired
phone's request asked for, the phone is locked as well. Rem saw that request, but sent
none of those files, and never sends them to a phone even when the path is named.

A flagged request is refused, and the phone is locked: the desktop refuses every
request from it until the user clicks Unlock in Settings > Devices > Paired devices.
The flagged text is kept encrypted in the device registry (owner-only), the desktop
log gets an entry without the text, and the window shows a notice.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

# The sentence a locked phone gets for every request. Phones match its start to show
# the locked state; older phones show it as the request's error.
LOCKED_PREFIX = "This phone is locked."
LOCKED_DETAIL = (
    LOCKED_PREFIX + " Your computer flagged a request from it as suspicious and stopped it. "
    "To unlock, open Raticode on your computer, then Settings > Devices > "
    "Paired devices, and choose Unlock."
)

# Requests unlike the phone's history flag only after this many screened requests.
BASELINE = 25
# "Recent" for the history signal: a class seen within this many requests is familiar.
WINDOW = 200
# At most this much text per request is screened; the protocol caps fields far lower.
MAX_SCREENED = 64 * 1024
# Flagged requests kept for review per phone; older ones are dropped.
KEEP_FLAGGED = 50

# Keys whose values are never screened. ``screen`` is text read from the phone's screen
# when the user shares it with Rem: page content the user is looking at, not a request.
# The rest are identifiers, revisions, links and paths the desktop checks on its own.
_SKIPPED_KEYS = frozenset(
    {
        "screen",
        "url",
        "href",
        "path",
        "attachment_ids",
        "request_ids",
        "known",
        "digest",
        "expected_revision",
        "updated_at",
        "timezone",
    }
)
# File transfers carry the phone's attachments and screenshots. Never screened.
_UNSCREENED_KINDS = frozenset(
    {"file.offer", "file.chunk", "file.request", "file.status", "file.cancel"}
)
# Text fields of the other request kinds.
_TEXT_FIELDS = {
    "chat.submit": ("text",),
    "job.submit": ("intent",),
    "approval.resolve": ("instruction",),
    "swarm.message": ("body",),
    "thread.create": ("title",),
}


def request_texts(event: dict[str, Any]) -> list[str]:
    """The words a phone request carries for Rem, an agent or a model."""
    kind = event.get("type")
    payload = event.get("payload")
    if kind in _UNSCREENED_KINDS or not isinstance(payload, dict):
        return []
    if kind in _TEXT_FIELDS:
        return [payload[k] for k in _TEXT_FIELDS[kind] if isinstance(payload.get(k), str)]
    if kind == "desktop.action":
        return list(_strings(payload.get("params"), 0))
    return []


def _strings(value: Any, depth: int) -> Iterator[str]:
    if depth > 6:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            # Identifiers (thread_id, message_id, request_ids…) are never words.
            if key not in _SKIPPED_KEYS and not str(key).endswith(("_id", "_ids")):
                yield from _strings(item, depth + 1)
    elif isinstance(value, list):
        for item in value[:200]:
            yield from _strings(item, depth + 1)


# --- Rules ------------------------------------------------------------------------

# Files that usually hold credentials. .env.example and friends are templates.
_SECRET_FILE = re.compile(
    r"""(?ix)
    (?:^|(?<=[\s'"`(=:/\\]))
    (?:
        \.ssh(?:/[\w.-]*)?
      | id_(?:rsa|dsa|ecdsa|ed25519)(?:_sk)?(?!\.pub)\b
      | \.aws/(?:credentials|config)\b
      | \.netrc\b | \.pgpass\b | \.git-credentials\b | \.npmrc\b | \.pypirc\b
      | \.docker/config\.json | \.kube/config\b | gh/hosts\.ya?ml
      | \.?gnupg\b | \.password-store\b
      | (?:[\w.-]*[-_.])?auth\.json\b | credentials?\.json\b | client_secrets?\.json\b
      | \.credentials\.json\b
      | secrets?\.(?:json|ya?ml|toml|env|txt)\b
      | service[-_]?account[\w.-]*\.json\b
      | \.env(?:\.(?!example\b|sample\b|template\b|dist\b|defaults?\b)[\w-]+)?(?![\w.-])
      | etc/(?:shadow|gshadow|sudoers)\b
      | [\w.-]+\.(?:pem|p12|pfx|p8|key|csr|cer|crt|kdbx|keystore|jks|ppk|asc)\b
      | [\w.-]+\.(?:mobileprovision|provisionprofile|keychain(?:-db)?)\b
      | devid(?:[_.-][\w.-]*)?\b
      | key4\.db\b | logins\.json\b | cookies\.sqlite\b | wallet\.dat\b
      | login\.keychain(?:-db)?\b
      | (?:gofer|raticode)[\w/.-]*(?:token|identity|storage[-_]key)[\w.-]*
    )
    """
)
# Verbs that show, send or copy out a file's contents.
_DUMP_VERB = re.compile(
    r"""(?ix)\b(?:
        cat|bat|strings|xxd|hexdump|base64|print|printf
      | show|display|dump|reveal|read\s+(?:me|out|back)
      | send|e-?mail|mail|upload|paste|forward|exfiltrate|leak
      | give\s+me|get\s+me|fetch\s+me|grab|scp|rsync|curl|wget
    )\b"""
)
# Commands that read a file named right after them: ``tail -n 5 ~/.netrc``.
_COMMAND_BEFORE = re.compile(
    r"(?i)\b(?:head|tail|less|more|nl|tac|od|cp|nc|type)\s+(?:-\S+\s+(?:\d+\s+)?)*[~$\w/.-]*$"
)
# Words between a verb and the file that make it a question, or about another thing.
_ABOUT_FILE = re.compile(
    r"(?i)\b(?:how|why|where|whether|if|which|when|about|explain|ignore|gitignore|commit\w*|"
    r"example|template|loads?|loading|parser|parsing|format|docs?|summary|report)\b"
)
_SECRET_NOUN = (
    r"(?:passwords?|passwds?|passphrases?|pass\s?codes?|pins?\b(?=\s+(?:code|number)s?)"
    r"|credentials?|creds|logins?\s+and\s+passwords?"
    r"|private\s+keys?|ssh\s+keys?|gpg\s+keys?|pgp\s+keys?|signing\s+keys?|secret\s+keys?"
    r"|seed\s+phrases?|recovery\s+(?:codes?|phrases?|keys?)|mnemonic|backup\s+codes?"
    r"|2fa\s+(?:codes?|secrets?|seeds?)|totp\s+(?:secrets?|seeds?)"
    r"|api\s+keys?|access\s+keys?|keychain|keyring"
    r"|(?:api|access|auth|bearer|oauth|refresh|session|github|gh|gitlab|npm|pypi|openai"
    r"|anthropic|aws|owner|pairing|personal\s+access|provider)\s+tokens?"
    r"|session\s+cookies?|secrets?(?:\s+values?)?|env(?:ironment)?\s+(?:vars|variables))"
)
# A request for credential values: "send me my passwords", "show the API keys".
_CRED_ASK = re.compile(
    r"""(?ix)\b(?:
        send|e-?mail|mail|text|dm|message|upload|post|paste|forward|share|hand\s+over
      | give|show|display|list|print|dump|reveal|tell|read\s+out|export|leak|exfiltrate
      | cat|fetch|grab|collect|retrieve|what(?:'s|\s+is|\s+are|\s+were)
    )\b
    (?P<gap>(?:\W+\w+){0,5}?)\W+
    (?P<noun>"""
    + _SECRET_NOUN
    + r""")\b(?P<after>(?:\W+\w+){0,2})""",
)
# Words between the verb and the noun that make it a question about the feature.
_HOWTO = re.compile(
    r"(?i)\b(?:how|where|why|whether|if|which|when|rotate|reset|change|add|set|setup|"
    r"configure|update|remove|delete|revoke|new|generate|create|no|not|never|without|"
    r"about|on|report|summary|audit|review|article|docs?|strong|random|good|secure|unique|"
    r"expired|expiring|leaked|weak|example|fake|test|dummy|sample)\b"
)
# Words right after the noun that make it about a feature, page or count.
_ABOUT = re.compile(
    r"(?i)^\W+(?:usage|used|count|limits?|budget|settings?|page|screen|fields?|policy|"
    r"manager|reset|rotation|expir\w*|format|prompts?|dialog|form|validation|length|"
    r"strength|requirements?|docs?|documentation|feature|support|flow|errors?|bugs?|"
    r"issues?|setup|guide|instructions?|section|tab|menu|option|names?|list\s+page|"
    r"storage\s+design|handling|check|scanner|rule|rules|leak\s+warning|generator|hint|"
    r"strength|expire\w*|work(?:s|ing)?|are\s+stored|is\s+stored)\b"
)
_NEGATED = re.compile(r"(?i)\b(?:never|don'?t|do\s+not|not|no|stop|without)\W+(?:\w+\W+){0,2}$")

# Files described rather than named: signing material, certificate and key containers,
# key stores and wallets. "Send me the devid files", "export the p12", "give me the
# signing cert". Apple's Developer ID files are often saved as devid.key, devid.p12…
_KEY_MATERIAL = (
    r"(?:devid|dev\s+id|developer\s+id)(?:\s+(?:application|installer))?"
    r"(?:\s+(?:files?|keys?|certs?|certificates?|identit(?:y|ies)|stuff|things|signing\s+\w+))?"
    r"|(?:code[\s-]?)?signing\s+(?:keys?|certs?|certificates?|identit(?:y|ies)|files?|materials?"
    r"|credentials?|stuff)"
    r"|(?:private|signing|developer|distribution|apple|client|code[\s-]?signing)\s+"
    r"(?:certs?|certificates?)"
    r"|(?:certs?|certificates?)\s+(?:files?|and\s+(?:private\s+)?keys?|bundles?)"
    r"|(?:key|secret|credential)\s+files?|key\s*stores?|key\s*chains?|key\s+pairs?"
    r"|provisioning\s+profiles?"
    r"|(?:ssh|gpg|pgp)\s+(?:private\s+)?keys?"
    r"|wallet\s+(?:files?|keys?|backups?|seeds?)|(?:crypto|bitcoin|ethereum|metamask)\s+wallets?"
    r"|\.?(?:p12|pfx|pem|p8|jks|csr|mobileprovision)(?:\s+files?)?"
)
_KEY_MATERIAL_ASK = re.compile(
    r"""(?ix)\b(?:
        send|e-?mail|mail|text|dm|message|upload|post|paste|forward|share|hand\s+over
      | give|show|display|print|dump|reveal|export|leak|exfiltrate|cat|fetch|grab|copy
      | transfer|attach|airdrop|deliver|zip|bring|get\s+me|move|sync|push|download
    )\b
    (?P<gap>(?:\W+\w+){0,5}?)\W+
    (?P<noun>"""
    + _KEY_MATERIAL
    + r""")\b(?P<after>(?:\W+\w+){0,2})""",
)
# Words between the verb and the described files that make it about something else.
_KEY_MATERIAL_ABOUT = re.compile(
    r"(?i)\b(?:how|where|why|whether|if|which|when|about|no|not|never|without|docs?|article|"
    r"guide|instructions?|steps|example|fake|test|dummy|sample|public|expir\w*)\b"
)
# A question about the files rather than a request for them: "how do I export the p12".
_QUESTION_BEFORE = re.compile(r"(?i)\b(?:how|why|where|which|whether|what\s+(?:does|do|is))\b")
# "Can I turn off the phone lock?", "should I disable the safeguard": the user asking
# about a setting, never Rem being told to get around it.
_ASKING_ABOUT = re.compile(r"(?i)\b(?:can|could|should|do|may)\s+i\b|\bis\s+it\s+(?:safe|ok)\b")

# Insisting after a refusal: "send them anyway", "do it anyway", "I said send them",
# "override". Flagged only within REFUSAL_WINDOW of a refusal answering this phone.
_INSIST = re.compile(
    r"""(?ix)
        \b(?:send|attach|share|give|forward|upload|e-?mail|do|show|export|just)\b
        (?:\W+\w+){0,4}?\W+(?:anyway|anyways|anyhow|regardless|either\s+way)\b
      | \bi\s+(?:said|told\s+you\s+to|asked\s+you\s+to)\W+(?:just\s+)?
        (?:send|attach|share|give|forward|upload|do\s+it|export)\b
      | \boverride\b
      | \b(?:ignore|bypass|skip|disable|turn\s+off|get\s+around|work\s+around)\b
        (?:\W+\w+){0,3}?\W+(?:safeguards?|safety|warnings?|checks?|protections?|guards?
        |filters?|restrictions?|rules?|block)\b
      | \bjust\s+(?:send|attach|give|share|forward)\s+(?:me\s+)?(?:it|them|those|these|that
        |the\s+files?|everything|all)\b
      | \bsend\s+(?:me\s+)?(?:them|those|these|it)\s+(?:all|now|already)\b
    """
)
# Asking to get past the sensitive-file safeguard flags without a refusal first.
_DEFEAT_SAFEGUARD = re.compile(
    r"""(?ix)\b(?:ignore|bypass|skip|disable|turn\s+off|override|get\s+around|work\s+around)\b
        (?:\W+\w+){0,4}?\W+(?:safeguards?|sensitive[\s-]files?\s+(?:check|rule|guard)s?
        |secret[\s-]files?\s+(?:check|rule|guard)s?|file\s+safeguards?|phone\s+(?:guard|lock))\b
    """
)
# A refusal answering a phone stays fresh this long for the insist rule.
REFUSAL_WINDOW = 60 * 60

_ENV_DUMP = re.compile(
    r"""(?imx)
        \bprintenv\b
      | ^\s*(?:sudo\s+)?env\s*[|>]
      | \bset\s*\|\s*grep\b
      | \becho\s+["']?\$\{?\w*(?:token|key|secret|pass(?:word)?|cred)\w*
      | /proc/(?:self|\d+|\*)/environ\b
      | \bsecurity\s+(?:find-(?:generic|internet)-password|dump-keychain)\b
      | \bsecret-tool\s+(?:lookup|search)\b
      | \bgh\s+auth\s+token\b
      | \bgit\s+credential\s+fill\b
      | \bkeyring\s+get\b
      | \baws\s+configure\s+(?:get|export-credentials)\b
      | \bcat\s+/etc/passwd\b
    """
)
_PIPE_SHELL = re.compile(
    r"""(?ix)
        \b(?:curl|wget|fetch|iwr|irm|invoke-webrequest|invoke-restmethod)\b
        [^\n|;]{0,400}\|\s*(?:sudo\s+(?:-\S+\s+)*)?
        (?:(?:ba|z|da|k|c|tc|fi)?sh|python\d?(?:\.\d+)?|perl|ruby|node|php|iex|powershell|pwsh)\b
      | \b(?:ba|z)?sh\s+(?:-c\s+)?["']?\$\(\s*(?:curl|wget)\b
      | \b(?:(?:ba|z)?sh|source|\.)\s+<\(\s*(?:curl|wget)\b
      | \biex\s*\(?\s*\(?\s*new-object\s+net\.webclient\)?\.downloadstring
    """
)
_REVERSE_SHELL = re.compile(
    r"""(?ix)
        /dev/(?:tcp|udp)/
      | \bn(?:c|cat|etcat)\b[^\n]{0,120}\s-[a-z]*[ec]\s
      | \bmkfifo\b[^\n]{0,200}\bn(?:c|cat|etcat)\b
      | \b(?:ba)?sh\s+-i\s*[>&]+
      | \bsocat\b[^\n]{0,160}\bexec:
      | \bsocket\.socket\b[^\n]{0,400}\b(?:dup2|subprocess|pty\.spawn)\b
      | \bpty\.spawn\s*\(
      | \s-e\s+/bin/(?:ba|z)?sh\b
      | \bpowershell(?:\.exe)?\b[^\n]{0,40}\s-(?:e|en|enc|encodedcommand)\s+[A-Za-z0-9+/=]{16,}
      | \bnet\.sockets\.tcpclient\b
      | \bmsfvenom\b | \bmeterpreter\b
    """
)
_DECODE_RUN = re.compile(
    r"""(?ix)
        \bbase64\s+(?:-d|--decode|-D)\b[^\n]{0,120}\|\s*(?:sudo\s+)?(?:(?:ba|z|da)?sh|python\d?|perl)\b
      | \beval\s*\(\s*(?:atob|base64_decode|buffer\.from|unescape)\b
      | \bexec\s*\(\s*(?:base64\.b64decode|__import__\s*\(\s*['"]base64|codecs\.decode)
      | \bfrombase64string\b
      | \beval\s+["']?\$\(\s*echo\b[^\n]{0,600}\bbase64\b
      | \becho\s+["']?[A-Za-z0-9+/=]{40,}["']?\s*\|\s*base64\s+(?:-d|--decode|-D)\b
    """
)
_PERSIST = re.compile(
    r"""(?ix)
        >>?\s*["']?(?:~|\$HOME|/root|/home/[^/\s]+|/Users/[^/\s]+)?/?\.ssh/authorized_keys
      | \b(?:add|append|write|put|echo|insert)\b[^\n]{0,120}\bauthorized_keys\b
    """
)
_URL = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*:)?//\S+|\bwww\.\S+")
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/_-]{200,}={0,2}")
_HEX_RUN = re.compile(r"(?i)(?:\\x[0-9a-f]{2}){24,}|\b[0-9a-f]{400,}\b")

# Weaker signals, used only for the history rule.
_SHELL = re.compile(
    r"(?:\|\s*(?:sh|bash|grep|awk|sed|xargs|tee|nc)\b|&&|\$\(|`[^`\n]{2,}`|\bsudo\b"
    r"|\bchmod\s+(?:\+x|[0-7]{3,4})\b|(?:^|\s)>{1,2}\s*/|\brm\s+-[rf]+\b|\bnohup\b)"
)
_RAW_IP = re.compile(
    r"(?i)\b(?:https?|ftp|tcp|udp|wss?)://(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b"
)
_LONG_TOKEN = re.compile(r"\S{80,}")
_ESCAPES = re.compile(r"(?i)(?:%[0-9a-f]{2}){10,}|(?:\\u[0-9a-f]{4}){8,}")
_SECRET_NOUN_ONLY = re.compile(r"(?i)\b" + _SECRET_NOUN + r"\b")


@dataclass(frozen=True)
class Verdict:
    """Why a request was flagged (empty when it passes) and its weaker signals."""

    rules: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    signals: frozenset[str] = field(default_factory=frozenset)

    @property
    def flagged(self) -> bool:
        return bool(self.rules)


RULE_REASONS = {
    "credential_request": "asks for passwords, keys, tokens or other credentials",
    "credential_file": "asks to show or send a file that usually holds credentials",
    "environment_dump": "asks to print environment variables or a keychain",
    "download_to_shell": "pipes a download straight into a shell",
    "reverse_shell": "looks like a reverse shell",
    "decode_and_run": "decodes hidden content and runs it",
    "ssh_persistence": "adds a key to SSH's authorized_keys",
    "encoded_blob": "carries a long encoded blob",
    "unlike_history": "mixes several things this phone has never sent before",
    "insist_after_refusal": "insists on files Rem had just held back",
    "defeat_safeguard": "asks Rem to get around its safeguards",
    "secret_file_refused": "asked for a file Rem held back because it may hold a key or password",
}


def _credential_request(text: str) -> bool:
    for found in _CRED_ASK.finditer(text):
        if _HOWTO.search(found.group("gap")) or _ABOUT.match(found.group("after") or ""):
            continue
        if _NEGATED.search(text[max(0, found.start() - 40) : found.start()]):
            continue
        return True
    return False


def _key_material(text: str) -> bool:
    for found in _KEY_MATERIAL_ASK.finditer(text):
        if _KEY_MATERIAL_ABOUT.search(found.group("gap")) or _ABOUT.match(
            found.group("after") or ""
        ):
            continue
        before = text[max(0, found.start() - 60) : found.start()]
        if _NEGATED.search(before):
            continue
        if _QUESTION_BEFORE.search(re.split(r"[!?\n;]|\.\s", before)[-1]):
            continue
        return True
    return False


def _defeat_safeguard(text: str) -> bool:
    """Asking Rem to get around its safeguards, not a question about the setting."""
    for found in _DEFEAT_SAFEGUARD.finditer(text):
        clause = re.split(r"[!?\n;]|\.\s", text[: found.start()])[-1]
        if _QUESTION_BEFORE.search(clause) or _ASKING_ABOUT.search(clause):
            continue
        return True
    return False


def _credential_file(text: str) -> bool:
    if _key_material(text):
        return True
    for found in _SECRET_FILE.finditer(text):
        before = text[max(0, found.start() - 80) : found.start()]
        if _COMMAND_BEFORE.search(before):
            return True
        # Only the clause the file is named in counts, from its last showing verb on.
        clause = re.split(r"[!?\n;]|\.\s", before)[-1]
        verbs = list(_DUMP_VERB.finditer(clause))
        if not verbs:
            continue
        if _ABOUT_FILE.search(clause[verbs[-1].end() :]):
            continue
        if _NEGATED.search(clause[: verbs[-1].start()]):
            continue
        return True
    return False


def _blob(text: str) -> bool:
    bare = _URL.sub(" ", text)
    if _HEX_RUN.search(bare):
        return True
    for run in _BASE64_RUN.findall(bare):
        if (
            re.search(r"[A-Z]", run)
            and re.search(r"[a-z]", run)
            and re.search(r"\d", run)
            and len(set(run)) >= 20
        ):
            return True
    return False


def signals(text: str) -> frozenset[str]:
    """The weaker signal classes in [text], for the history rule."""
    found = set()
    if _SHELL.search(text):
        found.add("shell")
    if _RAW_IP.search(text):
        found.add("raw_ip")
    if _LONG_TOKEN.search(_URL.sub(" ", text)):
        found.add("long_token")
    if _ESCAPES.search(text):
        found.add("escapes")
    if _SECRET_FILE.search(text):
        found.add("secret_file")
    if _SECRET_NOUN_ONLY.search(text):
        found.add("secret_word")
    return frozenset(found)


def screen(
    texts: list[str], profile: Profile | None = None, *, after_refusal: bool = False
) -> Verdict:
    """Screens a request's texts. [profile] is the phone's history, when known.

    [after_refusal] is true while a refusal of secret-looking files answering this
    phone is recent (REFUSAL_WINDOW): insisting then is flagged.
    """
    text = "\n".join(t for t in texts if t)[:MAX_SCREENED]
    if not text.strip():
        return Verdict()
    rules: list[str] = []
    if _credential_request(text):
        rules.append("credential_request")
    if _credential_file(text):
        rules.append("credential_file")
    if _ENV_DUMP.search(text):
        rules.append("environment_dump")
    if _PIPE_SHELL.search(text):
        rules.append("download_to_shell")
    if _REVERSE_SHELL.search(text):
        rules.append("reverse_shell")
    if _DECODE_RUN.search(text):
        rules.append("decode_and_run")
    if _PERSIST.search(text):
        rules.append("ssh_persistence")
    if _blob(text):
        rules.append("encoded_blob")
    if _defeat_safeguard(text):
        rules.append("defeat_safeguard")
    elif after_refusal and _INSIST.search(text):
        rules.append("insist_after_refusal")
    found = signals(text)
    if not rules and profile is not None and profile.unlike(found):
        rules.append("unlike_history")
    return Verdict(tuple(rules), tuple(RULE_REASONS[r] for r in rules), found)


@dataclass
class Profile:
    """How many requests a phone sent, and when each signal class last appeared."""

    seen: int = 0
    last: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, raw: str | None) -> Profile:
        try:
            data = json.loads(raw or "{}")
            last = {str(k): int(v) for k, v in dict(data.get("last") or {}).items()}
            return cls(max(0, int(data.get("seen") or 0)), last)
        except (TypeError, ValueError, AttributeError):
            return cls()

    def dump(self) -> str:
        return json.dumps({"seen": self.seen, "last": self.last}, separators=(",", ":"))

    def unlike(self, found: frozenset[str]) -> bool:
        """Two or more signal classes, none sent within the window, after the baseline."""
        if self.seen < BASELINE or len(found) < 2:
            return False
        return all(self.seen - self.last.get(name, -WINDOW - 1) > WINDOW for name in found)

    def record(self, found: frozenset[str]) -> None:
        self.seen += 1
        for name in found:
            self.last[name] = self.seen


class PhoneLocks:
    """Locked phones, flagged requests and each phone's signal history, in the registry.

    Flagged text is encrypted with the registry's storage key, like other device
    records, in a database only the user's account can read. Unlocking is a local
    desktop action (``DeviceControl.action``), which agent tokens can't reach.
    """

    def __init__(self, application: Any) -> None:
        self.app = application
        self.db = application.registry.db
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS device_phone_locks (peer TEXT PRIMARY KEY, "
            "locked_at REAL NOT NULL, rules TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS device_phone_flags (id TEXT PRIMARY KEY, "
            "peer TEXT NOT NULL, at REAL NOT NULL, rules TEXT NOT NULL, value BLOB NOT NULL);"
            "CREATE TABLE IF NOT EXISTS device_phone_profiles (peer TEXT PRIMARY KEY, "
            "value TEXT NOT NULL);"
            # One row while the user has turned the guard off. On by default.
            "CREATE TABLE IF NOT EXISTS device_phone_guard_off (only INTEGER PRIMARY KEY);"
            # When Rem last held back files answering this phone (see held_back).
            "CREATE TABLE IF NOT EXISTS device_phone_refusals (peer TEXT PRIMARY KEY, "
            "at REAL NOT NULL);"
        )

    # Local settings API only.
    def enabled(self) -> bool:
        with self.app.registry.lock:
            return self.db.execute("SELECT 1 FROM device_phone_guard_off").fetchone() is None

    def set_enabled(self, enabled: bool) -> None:
        with self.app.registry.transaction():
            if enabled:
                self.db.execute("DELETE FROM device_phone_guard_off")
            else:
                self.db.execute("INSERT OR IGNORE INTO device_phone_guard_off VALUES (1)")

    def locked(self, peer: str) -> bool:
        with self.app.registry.lock:
            return (
                self.db.execute("SELECT 1 FROM device_phone_locks WHERE peer=?", (peer,)).fetchone()
                is not None
            )

    def locks(self) -> list[dict[str, Any]]:
        with self.app.registry.lock:
            rows = self.db.execute(
                "SELECT peer, locked_at, rules FROM device_phone_locks ORDER BY locked_at"
            ).fetchall()
        return [
            {
                "device_id": row[0],
                "locked_at": row[1],
                "rules": json.loads(row[2]),
                "reasons": [RULE_REASONS.get(r, r) for r in json.loads(row[2])],
            }
            for row in rows
        ]

    def unlock(self, peer: str) -> None:
        """The user's own Unlock in Paired devices. The flagged records stay for review."""
        with self.app.registry.transaction():
            self.app.registry._row(peer)
            self.db.execute("DELETE FROM device_phone_locks WHERE peer=?", (peer,))
            # The user's unlock settles the refusal too, so their next words aren't
            # taken as insisting.
            self.db.execute("DELETE FROM device_phone_refusals WHERE peer=?", (peer,))
        logger.warning("Paired phone %s unlocked from the desktop.", _short(peer))

    def flagged(self, peer: str) -> list[dict[str, Any]]:
        result = []
        with self.app.registry.lock:
            rows = self.db.execute(
                "SELECT id, at, rules, value FROM device_phone_flags WHERE peer=? ORDER BY at DESC",
                (peer,),
            ).fetchall()
        for row in rows:
            try:
                value = self.app._decode(peer, row[3])
            except Exception:  # noqa: BLE001
                value = {"text": "", "kind": "unknown", "op": ""}
            rules = json.loads(row[2])
            result.append(
                {
                    "id": row[0],
                    "at": row[1],
                    "rules": rules,
                    "reasons": [RULE_REASONS.get(r, r) for r in rules],
                    "kind": value.get("kind", ""),
                    "op": value.get("op", ""),
                    "text": value.get("text", ""),
                }
            )
        return result

    def counts(self) -> dict[str, int]:
        with self.app.registry.lock:
            rows = self.db.execute(
                "SELECT peer, COUNT(*) FROM device_phone_flags GROUP BY peer"
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def forget_flagged(self, peer: str) -> None:
        with self.app.registry.transaction():
            self.db.execute("DELETE FROM device_phone_flags WHERE peer=?", (peer,))

    def forget_peer(self, peer: str) -> None:
        """Unpairing removes the phone's lock, history and flagged records."""
        for table in (
            "device_phone_locks",
            "device_phone_flags",
            "device_phone_profiles",
            "device_phone_refusals",
        ):
            self.db.execute(f"DELETE FROM {table} WHERE peer=?", (peer,))

    def check(self, peer: str, event: dict[str, Any]) -> str | None:
        """``phone_locked``, ``suspicious_request`` or None, for a new phone request.

        Runs in its own transaction before the request is handled, so a lock is kept
        even though the request itself is refused.
        """
        registry = self.app.registry
        with registry.transaction():
            row = registry._row(peer)
            if row["role"] != "controller" or row["state"] != "active":
                return None
            for table in ("device_inbox", "device_inbox_reads"):
                if self.db.execute(
                    f"SELECT 1 FROM {table} WHERE peer=? AND id=?", (peer, event.get("id"))
                ).fetchone():
                    # A resend: answered as before by _handle or _refuse.
                    return "phone_locked" if self.locked(peer) else None
                if self.db.execute(
                    f"SELECT 1 FROM {table} WHERE peer=? AND sequence=?",
                    (peer, event.get("sequence")),
                ).fetchone():
                    # _handle refuses a reused sequence number without acting on it.
                    return None
            if self.locked(peer):
                return "phone_locked"
            if not self.enabled():
                return None
            texts = request_texts(event)
            if not texts:
                return None
            stored = self.db.execute(
                "SELECT value FROM device_phone_profiles WHERE peer=?", (peer,)
            ).fetchone()
            profile = Profile.load(stored[0] if stored else None)
            refused = self.db.execute(
                "SELECT at FROM device_phone_refusals WHERE peer=?", (peer,)
            ).fetchone()
            recent = refused is not None and registry.clock() - refused[0] < REFUSAL_WINDOW
            verdict = screen(texts, profile, after_refusal=recent)
            if not verdict.flagged:
                profile.record(verdict.signals)
                self.db.execute(
                    "INSERT OR REPLACE INTO device_phone_profiles VALUES (?,?)",
                    (peer, profile.dump()),
                )
                return None
            payload = event.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            op = str(payload.get("op") or "") if event.get("type") == "desktop.action" else ""
            record = {
                "kind": str(event.get("type") or ""),
                "op": op,
                "text": "\n".join(texts)[:MAX_SCREENED],
            }
            self._lock(peer, verdict.rules, record)
        self._log_lock(peer, f"a {record['op'] or record['kind']} request", verdict.reasons, False)
        return "suspicious_request"

    def held_back(self, request: str, text: str, names: list[str], *, secret: bool) -> bool:
        """Rem held back files it was asked for; locks the phone that asked, if one did.

        Called by Rem (``RemHome.phone_refusal``) when its sensitive-file safeguard
        refuses files for a request, or its reply declines to send secret-looking files.
        [request] is the Rem chat request ID; only a paired phone's own request
        (``device_rem_requests``) counts. Every such refusal makes insisting on the
        phone suspicious for REFUSAL_WINDOW. A [secret] refusal (a key, certificate,
        password store or other secret-looking file) locks the phone at once: a phone
        asking for those is what this guard is for. Returns whether it locked one.
        """
        registry = self.app.registry
        with registry.transaction():
            found = self.db.execute(
                "SELECT peer FROM device_rem_requests WHERE request=? LIMIT 1", (request,)
            ).fetchone()
            if found is None:
                return False
            peer = str(found[0])
            row = self.db.execute(
                "SELECT role, state FROM peers WHERE device_id=?", (peer,)
            ).fetchone()
            if row is None or row[0] != "controller" or row[1] != "active":
                return False
            if not self.enabled():
                return False
            if self.locked(peer):
                return False
            now = registry.clock()
            self.db.execute(
                "INSERT OR REPLACE INTO device_phone_refusals VALUES (?,?)", (peer, now)
            )
            if not secret:
                return False
            held = ", ".join(n[:200] for n in names[:20])
            record = {
                "kind": "desktop.action",
                "op": "rem.message",
                "text": (text[:MAX_SCREENED] + ("\n\nHeld back: " + held if held else "")),
            }
            self._lock(peer, ("secret_file_refused",), record)
        self._log_lock(peer, "a rem.message request", (RULE_REASONS["secret_file_refused"],), True)
        return True

    def _lock(self, peer: str, rules: tuple[str, ...], record: dict[str, str]) -> None:
        """Keeps [record] for review and locks [peer]. Inside the registry transaction."""
        now = self.app.registry.clock()
        encoded = json.dumps(list(rules))
        self.db.execute(
            "INSERT INTO device_phone_flags VALUES (?,?,?,?,?)",
            (str(uuid4()), peer, now, encoded, self.app._encode(peer, record)),
        )
        self.db.execute(
            "DELETE FROM device_phone_flags WHERE peer=? AND id NOT IN (SELECT id FROM "
            "device_phone_flags WHERE peer=? ORDER BY at DESC LIMIT ?)",
            (peer, peer, KEEP_FLAGGED),
        )
        self.db.execute(
            "INSERT OR REPLACE INTO device_phone_locks VALUES (?,?,?)", (peer, now, encoded)
        )

    def _log_lock(self, peer: str, what: str, reasons: tuple[str, ...], seen: bool) -> None:
        name = self.db.execute("SELECT name FROM peer_names WHERE device_id=?", (peer,)).fetchone()
        # The log names the phone and the rules, never the text, which may hold secrets.
        logger.warning(
            "Locked paired phone %s (%s): %s %s. %s Review it and unlock the phone in "
            "Settings > Devices > Paired devices.",
            (name[0] if name else None) or "Phone",
            _short(peer),
            what,
            "; ".join(reasons),
            "Rem held the files back." if seen else "Rem never saw it.",
        )


def _short(peer: str) -> str:
    return peer[:8]
