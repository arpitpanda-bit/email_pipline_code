
import json, re, unicodedata, argparse, sys
from datetime import datetime
from pathlib import Path

try:
    # pyrefly: ignore [missing-import]
    from bs4 import BeautifulSoup, Comment, Tag
except ImportError:
    sys.exit("Run: pip install beautifulsoup4 lxml")

try:
    # pyrefly: ignore [missing-import]
    import html2text as _h2t
except ImportError:
    sys.exit("Run: pip install html2text")


# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════

# Layer 0 — HTML classes to strip (multi-client)
_QUOTE_CLASSES = {
    # Gmail
    "gmail_quote", "gmail_quote_container", "gmail_ad_hoc_content",
    "gmail_ad_hoc_v2_content", "gmail_chip",
    # Yahoo
    "yahoo_quoted",
    # Outlook / Hotmail
    "OutlookMessageHeader",
    # Apple Mail
    "AppleOriginalContents",
    # Thunderbird
    "moz-cite-prefix",
    # ProtonMail
    "protonmail_quote",
    # Generic
    "quoted-text", "email-quoted-reply",
}
_SIG_CLASSES = {
    # Gmail
    "gmail_signature", "gmail_signature_prefix",
    # Outlook
    "Signature", "OutlookSignature",
    # Apple Mail
    "AppleMailSignature",
    # ProtonMail
    "protonmail_signature_block",
    # Generic
    "signature", "sig", "email-signature", "email_signature",
}

# Layer 2 — Reply-chain triggers (matched against start of line)
_REPLY_TRIGGERS = [
    # English: "On Apr 7, 2026, Paul wrote:"
    re.compile(r"^on\s+.{5,120}\s+wrote\s*:\s*$", re.I),
    # Forwarded message headers
    re.compile(r"^-{3,}\s*(original|forwarded)\s+message\s*-{3,}", re.I),
    # Outlook-style: "From: X ... Sent: ..."
    re.compile(r"^from\s*:\s+\S+.*\n?sent\s*:\s+", re.I | re.M),
    # Bold-formatted Outlook headers (markdown): "**From:** X"
    re.compile(r"^\*{1,2}from\*{0,2}\s*:\*{0,2}\s+\S+", re.I),
    # International Outlook (French/German/Spanish/Portuguese)
    re.compile(r"^(de|von|da)\s*:\s+\S+", re.I),
    # Quoted lines
    re.compile(r"^>{1,}", re.M),
    # Sig separators
    re.compile(r"^\\--\s*$"),
    re.compile(r"^--\s*$"),
    # Apple Mail / Thunderbird
    re.compile(r"^begin\s+forwarded\s+message", re.I),
    # Auto-generated meeting / calendar invitations
    re.compile(r"^.{0,40}inviting you to a scheduled", re.I),
    re.compile(r"^join\s+(zoom|microsoft\s+teams|webex|google\s+meet)\s+(meeting|call)", re.I),
    re.compile(r"^invitation\s+from\s+\[?google\s+calendar", re.I),
    re.compile(r"^you[\u2019']?re\s+invited\s+to", re.I),
]

# Layer 3 — Disclaimer trigger keywords
_DISCLAIMER_TRIGGERS = [
    re.compile(r"\bdisclaimer\s*:", re.I),
    re.compile(r"\bconfidentiality\s*(notice|statement)?\s*:", re.I),
    re.compile(r"\blegal\s*(notice|disclaimer)\s*:", re.I),
    re.compile(r"\b(alert|caution|warning|important\s*(notice|disclaimer)?|privilege[d]?\s*(notice)?)\s*:", re.I),
    re.compile(r"\bprivate\s*[&]?\s*confidential\b", re.I),
    re.compile(r"\b(originated\s+outside|external\s+(sender|email))\b", re.I),
    re.compile(r"\b(clicking\s+any\s+link|opening\s+an\s+attachment)\b", re.I),
    re.compile(r"\bthis\s+(e-?mail|message|communication)\s+(is|may\s+be)\s+(confidential|privileged)", re.I),
    re.compile(r"\bif\s+you\s+(are\s+not\s+the\s+intended|have\s+received\s+this|received\s+this\s+in\s+error)", re.I),
    re.compile(r"\bany\s+(use|disclosure|distribution|copying)\s+(of\s+this|is)\s+(prohibited|unauthorized|unlawful)", re.I),
    re.compile(r"\bplease\s+(delete|destroy)\s+(all\s+copies|this\s+(message|e-?mail))", re.I),
    re.compile(r"\bunauthorized\s+(use|disclosure|distribution)\s+is\s+(prohibited|strictly)", re.I),
    re.compile(r"\bnotify\s+the\s+(sender|author)\s+immediately", re.I),
]

# Layer 3 — Vocabulary that means a line is still part of a disclaimer block
_DISCLAIMER_VOCAB = [
    re.compile(r"\b(confidential|privileged|proprietary|classified)\b", re.I),
    re.compile(r"\b(unauthorized|unintended|intended\s+recipient)\b", re.I),
    re.compile(r"\b(prohibited|strictly\s+prohibited|illegal|unlawful)\b", re.I),
    re.compile(r"\b(please\s+(delete|destroy|notify|disregard))\b", re.I),
    re.compile(r"\b(notify\s+the\s+sender|contact\s+the\s+sender)\b", re.I),
    re.compile(r"\b(all\s+copies|this\s+(message|email|communication|transmission))\b", re.I),
    re.compile(r"\b(disclosure|dissemination|distribution|copying)\s+(is|are|of\s+this)\b", re.I),
    re.compile(r"\b(virus|malware|scanned|security\s+software)\b", re.I),
    re.compile(r"\b(legal|legally\s+binding|attorney|solicitor|counsel|jurisdiction)\b", re.I),
    re.compile(r"\b(tax\s+advice|regulated\s+(activity|firm)|financial\s+conduct|authority)\b", re.I),
]

# Layer 4 — Signature salutations
_SIG_SALUTATIONS = re.compile(
    r"^\s*("
    r"best\s*(regards?|wishes?)?|kind\s+regards?|warm\s+regards?|regards?|"
    r"sincerely|cheers|"
    # Compound thanks / thank-you variants
    r"(thanks?|thank\s+you)\s+(again|a\s+lot|so\s+much|a\s+bunch|a\s+ton|very\s+much|in\s+advance|for\s+everything)|"
    r"many\s+thanks(\s+again)?|"
    r"thanks?|thank\s+you|"
    r"yours?\s+(truly|faithfully|sincerely)?|"
    r"speak\s+soon|talk\s+soon|chat\s+soon|catch\s+up\s+soon|"
    r"looking\s+forward(\s+to\s+(hearing|connecting|speaking|chatting))?|"
    r"have\s+a\s+(great|good|wonderful|nice)\s+(day|week|weekend)|"
    r"all\s+the\s+best|with\s+(regards?|appreciation|gratitude)|"
    r"cordially|respectfully|faithfully|"
    r"(thanks?|thank\s+you)\s+and\s+(best\s+)?(regards?|wishes?)|"
    r"with\s+thanks\s+and\s+(best\s+)?(regards?|wishes?)|"
    r"thank\s+you\s+for\s+(your\s+)?(time|support|understanding|help|patience)"
    r")\s*[,\.!]?\s*$",
    re.I,
)

_SIG_INLINE = re.compile(
    r"^\s*(best|regards?|thanks?|thank\s+you|cheers|sincerely|warm\s+regards?)"
    r"\s*[,\.]\s*\*{0,2}[A-Z]",
    re.I,
)

# Layer 4 — Contact-card line patterns
_CONTACT_PATTERNS = [
    re.compile(r"\b\d{3}[\s\-\.]\d{3}[\s\-\.]\d{4}\b"),
    re.compile(r"\+\d[\d\s\-\(\)\.]{6,}"),
    re.compile(r"\*\+?\d[\d\s\-\(\)\.]{5,}\*"),
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"https?://\S+"),
    re.compile(r"\bwww\.[a-z0-9\-]+\.[a-z]{2,}", re.I),
    re.compile(r"linkedin\.com|twitter\.com|facebook\.com", re.I),
    re.compile(r"\b(vp|svp|evp|ceo|cto|cmo|cfo|coo|cio|ciso"
               r"|director|manager|president|chairman"
               r"|analyst|consultant|consulting|engineer|executive|specialist"
               r"|architect|coordinator|administrator|advisor|advisory"
               r"|representative|strategist|evangelist|principal"
               r"|account\s+executive|customer\s+success|sales\s+engineer"
               r"|solutions?\s+(architect|engineer|consultant)"
               r"|technical\s+(lead|director|manager|advisor)"
               r"|enterprise\s+(architect|consultant|representative))\b", re.I),
    re.compile(r"\b(suite|ste|floor|blvd|ave|road|drive)\b", re.I),
    re.compile(r"\b(inc|llc|ltd|corp|co|gmbh|plc|pvt|pte)\b\.?", re.I),
]

_AUTO_FOOTERS = re.compile(
    r"^\s*("
    r"sent\s+from\s+(my\s+)?(iphone|ipad|android|samsung|blackberry|galaxy|pixel|huawei|oneplus|xiaomi|motorola|nokia)"
    r"|get\s+(outlook|gmail)\s+for\s+(ios|android)"
    r"|sent\s+from\s+(outlook|yahoo\s+mail|mail)\s+for\s+(windows|mac|ios|android)"
    r"|sent\s+from\s+(protonmail|zoho\s+mail|aol\s+mail|thunderbird)"
    r"|sent\s+via\s+(yahoo|zoho|aol)"
    r"|\[cid:.*\]"
    r")\s*$",
    re.I,
)

# Layer 4 — Trailing name patterns
_NAME_LINE = re.compile(r"^[A-Z][a-zA-Z'-]+(?:\s+[A-Z][a-zA-Z'-]+){0,3}$")
_TEAM_LINE = re.compile(r"^\w[\w\s]{0,40}\s+Team$", re.I)

# Layer 5 — Line noise
_SEPARATOR_LINE = re.compile(r"^\s*[-_=*\u2500-\u2503\u2550-\u256c]{4,}\s*$|^\s*([*\-_]\s+){2,}[*\-_]?\s*$")
_PIPE_ONLY = re.compile(r"^\s*[\|\s\-:=]+\s*$")
_HASHTAG_LINE = re.compile(r"^(\s*#\w+){2,}\s*$")
# Lines of pipes + empty markdown links [](url) — HTML table signature artifacts
_PIPE_LINKS = re.compile(r"^[\s\|]*(\[.*?\]\(.*?\)[\s\|]*)+$")
_EMPTY_LINK_LINE = re.compile(r"^\s*\[\s*\]\(.*?\)\s*$")
# Empty bold/italic markdown artifacts (standalone ** or *** with no text)
_EMPTY_MD_FORMAT = re.compile(r"^\s*\*{1,3}\s*\*{0,3}\s*$")

# Layer 6 — Unicode map
_UNICODE_MAP = {
    "\u00a0": " ", "\u202f": " ", "\u200b": "", "\u200c": "",
    "\u200d": "", "\ufeff": "", "\u2019": "'", "\u2018": "'",
    "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "--",
    "\u2026": "...", "\u00b7": "-",
}


# ═══════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════

def _is_html(text: str) -> bool:
    return bool(re.search(r"<(html|body|div|p|br|span|table|a)\b", text, re.I))


def _normalise_unicode(text: str) -> str:
    for ch, rep in _UNICODE_MAP.items():
        text = text.replace(ch, rep)
    text = unicodedata.normalize("NFKD", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text


def _remove_escape_literals(text: str) -> str:
    """Remove \\r\\n and \\uXXXX that appear as literal strings."""
    text = text.replace("\\r\\n", " ").replace("\\r", " ").replace("\\n", " ")
    text = re.sub(r"\\r\\n|\\r|\\n", " ", text)

    def _decode(m):
        code = int(m.group(0)[2:], 16)
        if 0xD800 <= code <= 0xDFFF or code < 0x20:
            return " "
        return chr(code)

    text = re.sub(r"\\u[0-9a-fA-F]{4}", _decode, text)
    return text


def _clean_links(text: str) -> str:
    """Clean mailto links and broken empty-href links."""
    text = re.sub(r"\[([^\]]+)\]\(mailto:[^)]+\)", r"\1", text)
    text = re.sub(r"<mailto:[^>]+>", "", text)
    text = re.sub(r"mailto:[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", "", text)
    # Broken empty links: [text]("") or [text](\"\"")
    text = re.sub(r'\[([^\]]+)\]\(["\'\\ ]*\)', r'\1', text)
    return text


def _collapse_whitespace(text: str) -> str:
    lines = text.split("\n")
    out, blanks = [], 0
    for line in lines:
        s = line.rstrip()
        if s == "":
            blanks += 1
            if blanks <= 2:
                out.append("")
        else:
            blanks = 0
            out.append(s)
    return "\n".join(out).strip()


# ═══════════════════════════════════════════════════════════════════
# LAYER 0 — HTML structural removal
# ═══════════════════════════════════════════════════════════════════

def _strip_html(raw: str, keep_history: bool = True) -> str:
    soup = BeautifulSoup(raw, "lxml")

    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()

    for tag in soup.find_all(True):
        if not isinstance(tag, Tag) or not tag.attrs:
            continue
        classes = set(tag.get("class") or [])
        target_classes = _SIG_CLASSES if keep_history else (_QUOTE_CLASSES | _SIG_CLASSES)
        if classes & target_classes:
            tag.decompose()

    if not keep_history:
        for bq in soup.find_all("blockquote"):
            bq.decompose()

    for tag in soup.find_all(["script", "style", "head", "meta", "link", "noscript", "img"]):
        tag.decompose()

    strip_attrs = {"style", "class", "id", "dir", "role", "align",
                   "bgcolor", "width", "height", "valign", "border",
                   "cellpadding", "cellspacing", "tabindex", "rel"}
    for tag in soup.find_all(True):
        if isinstance(tag, Tag) and tag.attrs:
            for attr in strip_attrs:
                tag.attrs.pop(attr, None)

    for tag in soup.find_all(["span", "font"]):
        tag.unwrap()

    body = soup.find("body")
    return str(body) if body else str(soup)


# ═══════════════════════════════════════════════════════════════════
# LAYER 1 — HTML -> Markdown
# ═══════════════════════════════════════════════════════════════════

def _make_converter():
    h = _h2t.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.ignore_tables = False
    h.body_width = 0
    h.unicode_snob = True
    h.skip_internal_links = True
    h.inline_links = True
    h.wrap_links = False
    h.ul_item_mark = "-"
    h.ignore_emphasis = True
    return h

_CONVERTER = _make_converter()


def _html_to_md(html: str) -> str:
    return _CONVERTER.handle(html)


# ═══════════════════════════════════════════════════════════════════
# LAYER 2 — Reply-chain truncation
# ═══════════════════════════════════════════════════════════════════

def _remove_reply_chain(text: str) -> str:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        for pat in _REPLY_TRIGGERS:
            if pat.match(line.strip()):
                return "\n".join(lines[:i])
    return text


# ═══════════════════════════════════════════════════════════════════
# LAYER 3 — Disclaimer removal (line-level with vocabulary check)
# ═══════════════════════════════════════════════════════════════════

def _is_disclaimer_content(line: str) -> bool:
    return (
        any(p.search(line) for p in _DISCLAIMER_TRIGGERS) or
        any(p.search(line) for p in _DISCLAIMER_VOCAB)
    )


def _remove_disclaimers(text: str) -> str:
    """
    Remove disclaimer blocks line by line.

    When a trigger is found, skip lines until either:
      - A blank line (structural boundary), or
      - A line that doesn't contain legal vocabulary (keep that line).

    This preserves content before AND after inline disclaimers.

    Example:
      "Hi Arpit\\n disclaimer:\\nThis is confidential...\\n Hi Darshil"
      -> "Hi Arpit\\nHi Darshil"
    """
    lines = text.split("\n")
    result = []
    in_disclaimer = False

    for line in lines:
        s = line.strip()
        check_str = re.sub(r"^[\s>]*", "", line).strip(">*_ \t\n\r")

        if in_disclaimer:
            if check_str == "":
                in_disclaimer = False
                result.append("")
            elif _is_disclaimer_content(check_str):
                pass  # skip
            else:
                in_disclaimer = False
                result.append(line)
            continue

        if any(p.search(check_str) for p in _DISCLAIMER_TRIGGERS):
            in_disclaimer = True
            continue

        result.append(line)

    return "\n".join(result)


# ═══════════════════════════════════════════════════════════════════
# LAYER 4 — Signature removal
# ═══════════════════════════════════════════════════════════════════

def _strip_md_formatting(s: str) -> str:
    """Remove markdown bold/italic wrappers for clean pattern matching."""
    s = re.sub(r'^\*{1,2}|\*{1,2}$', '', s)
    s = re.sub(r'^_{1,2}|_{1,2}$', '', s)
    return s.strip()


def _is_contact_line(line: str) -> bool:
    s = _strip_md_formatting(line.strip())
    return bool(s) and any(p.search(s) for p in _CONTACT_PATTERNS)


def _is_sig_component(line: str) -> bool:
    """Check if a line is any kind of signature component.

    Covers: names, team lines, contact cards, separators,
    promotional/marketing lines wrapped in markdown bold/italic,
    and empty markdown formatting artifacts.
    """
    s = line.strip()
    if not s:
        return True  # blank lines between sig components
    # Empty markdown formatting artifacts (**, ***, __, ___)
    if re.match(r'^[*_]{1,3}\s*[*_]{0,3}$', s):
        return True
    # Standard signature components
    clean = _strip_md_formatting(s)
    if (_NAME_LINE.match(s) or _NAME_LINE.match(clean) or
            _TEAM_LINE.match(s) or _TEAM_LINE.match(clean) or
            _is_contact_line(line) or _SEPARATOR_LINE.match(s)):
        return True
    # Lines entirely wrapped in markdown bold/italic (promotional/branding)
    if re.match(r'^\*{1,2}.+\*{1,2}$', s) or re.match(r'^_{1,2}.+_{1,2}$', s):
        return True
    # Inline name-company signature: "**Name** | Company" (short company, no extra pipes)
    if re.match(r'^(\*{1,2})?[A-Z][a-zA-Z\'-]+\s+[A-Z][a-zA-Z\'-]+(\*{1,2})?\s*\|\s*\w[\w\s]{0,25}$', s):
        return True
    return False


def _remove_signature(text: str, keep_history: bool = True) -> str:
    lines = text.split("\n")
    result = []
    in_sig = False

    for line in lines:
        stripped = line.strip()

        if not keep_history and _SIG_INLINE.match(stripped):
            break

        if _SIG_SALUTATIONS.match(stripped):
            in_sig = True
            continue

        if _AUTO_FOOTERS.match(stripped):
            in_sig = True
            continue

        if in_sig:
            if _is_contact_line(line) or stripped == "":
                continue
            else:
                in_sig = False
                result.append(line)
        else:
            result.append(line)

    return "\n".join(result)


def _remove_trailing_names(text: str) -> str:
    """Strip trailing signature blocks: names, titles, contact lines, separators.

    Two-phase approach for maximum coverage:
    1. Forward scan — find a name line near the end followed by contact/title
       lines and truncate from there (handles signatures with promotional
       content or other non-standard lines below the contact info).
    2. Bottom-up strip — remove any remaining trailing signature components.
    """
    lines = text.split("\n")

    # ── Phase 1: Forward scan for anchorless signature blocks ──
    # Look at the last ~15 non-blank lines for a Name followed by
    # contact-like lines; if found, truncate from the name onward.
    non_blank = [(i, lines[i].strip()) for i in range(len(lines))
                 if lines[i].strip()]
    if len(non_blank) > 3:
        window = non_blank[-15:]  # search the tail
        for widx in range(len(window) - 1, -1, -1):
            line_idx, s = window[widx]
            clean = _strip_md_formatting(s)
            # pyrefly: ignore [parse-error]
            if _NAME_LINE.match(s) or _NAME_LINE.match(clean):
                # Count contact-like lines... for now, we just pass
                pass
    return text

def _remove_line_noise(text: str) -> str:
    lines = text.split("\n")
    out = []
    for line in lines:
        line = re.sub(r"^\s*\|+\s*", "", line)  # strip leading pipes
        line = re.sub(r"\s*\|+\s*$", "", line)  # strip trailing pipes
        line = re.sub(r"<[a-zA-Z/!][^>@]*>", "", line)
        line = re.sub(r"&[a-zA-Z]+;|&#\d+;|&#x[0-9a-fA-F]+;", " ", line)
        # Skip lines that are only pipes, dashes, whitespace, colons, or equals
        if _PIPE_ONLY.match(line):
            continue
        # Skip lines that are just a lone pipe
        if line.strip() == "|":
            continue
        out.append(line)
    return "\n".join(out)


# Layer 7 — Reply Header Stripping (when keeping history)
_REPLY_HEADER_PATTERN = re.compile(
    r"^[\s>]*\*{0,2}("
    r"From|Sent|To|Cc|Bcc|Subject|Date"
    r"|Von|Gesendet|An|Betreff|Datum"
    r"|De|Envoyé|À|Objet"
    r"|Enviado|Para|Asunto|Fecha|Data"
    r")\*{0,2}\s*:\s*",
    re.I
)

_ON_WROTE_PATTERN = re.compile(
    r"^[\s>]*On\s+.{5,180}\s+wrote\s*:\s*$",
    re.I
)

_FORWARDED_PATTERN = re.compile(
    r"^[\s>]*-{3,}\s*(original|forwarded)\s+message\s*-{3,}",
    re.I
)


_EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")

def _extract_email(s: str) -> str:
    match = _EMAIL_REGEX.search(s)
    if match:
        return match.group(0).lower().strip("<>(): \t_")
    return ""


def _remove_reply_headers(text: str) -> str:
    """Remove From/To/Sent/Subject headers and Gmail 'On... wrote:' lines."""
    lines = text.split("\n")
    out = []
    for line in lines:
        s = line.strip()
        if _REPLY_HEADER_PATTERN.match(s):
            continue
        if _ON_WROTE_PATTERN.match(s):
            continue
        if _FORWARDED_PATTERN.match(s):
            continue
        out.append(line)
    return "\n".join(out)


# Layer 8 — Inline Signature Stripping (middle of thread)
def _clean_for_check(line: str) -> str:
    s = re.sub(r"^[\s>]*", "", line)
    s = s.strip(">*_ \t\n\r")
    return s


def _classify_line(line: str) -> str:
    if "__REPLY_HEADER_PLACEHOLDER_" in line:
        return "PLACEHOLDER"
    s = _clean_for_check(line)
    if not s:
        return "EMPTY"
    if s.startswith("-") and len(s) < 30:
        return "SIGN_OFF"
    if _SIG_SALUTATIONS.match(s):
        return "SALUTATION"
    if _is_contact_line(line):
        return "CONTACT"
    if len(s) < 80:
        if s and s[0].islower():
            return "OTHER"
        if s[-1] in (".", "!", "?"):
            if len(s) < 15 and s[-1] == ".":
                pass
            else:
                return "OTHER"
        return "NAME_TITLE"
    return "OTHER"


def _remove_inline_signatures(text: str) -> str:
    """Surgically strip inline signature blocks from the middle of the thread."""
    lines = text.split("\n")
    lines_info = []
    for line in lines:
        cls = _classify_line(line)
        lines_info.append({
            "line": line,
            "class": cls,
            "remove": False
        })

    n = len(lines_info)

    # Phase 1: Contact-based signatures
    for i in range(n):
        if lines_info[i]["class"] == "CONTACT":
            sig_indices = {i}

            # Look upwards for up to 3 non-empty lines of NAME_TITLE, SALUTATION, CONTACT or SIGN_OFF
            up_count = 0
            curr = i - 1
            while curr >= 0 and up_count < 3:
                cls = lines_info[curr]["class"]
                if cls == "EMPTY":
                    curr -= 1
                    continue
                if cls in ("NAME_TITLE", "SALUTATION", "CONTACT", "SIGN_OFF"):
                    sig_indices.add(curr)
                    up_count += 1
                    curr -= 1
                else:
                    break

            # Look downwards for up to 2 non-empty lines of CONTACT, NAME_TITLE or SIGN_OFF
            down_count = 0
            curr = i + 1
            while curr < n and down_count < 2:
                cls = lines_info[curr]["class"]
                if cls == "EMPTY":
                    curr += 1
                    continue
                if cls in ("CONTACT", "NAME_TITLE", "SIGN_OFF"):
                    sig_indices.add(curr)
                    down_count += 1
                    curr += 1
                else:
                    break

            for idx in sig_indices:
                if lines_info[idx]["class"] != "PLACEHOLDER":
                    lines_info[idx]["remove"] = True

    # Phase 2: Salutation-based sign-offs (e.g. Thanks, \n T.J.)
    for i in range(n):
        if lines_info[i]["class"] == "SALUTATION" and not lines_info[i]["remove"]:
            sig_indices = {i}

            # Look downwards for up to 2 non-empty lines. If they are NAME_TITLE, include them.
            down_count = 0
            curr = i + 1
            while curr < n and down_count < 2:
                cls = lines_info[curr]["class"]
                if cls == "EMPTY":
                    curr += 1
                    continue
                if cls == "NAME_TITLE":
                    sig_indices.add(curr)
                    down_count += 1
                    curr += 1
                else:
                    break

            # Mark for removal if we found a name following the salutation
            if len(sig_indices) > 1:
                for idx in sig_indices:
                    if lines_info[idx]["class"] != "PLACEHOLDER":
                        lines_info[idx]["remove"] = True

    # Phase 3: Direct Hyphen-based Sign-offs (e.g. -Gwen)
    for i in range(n):
        if lines_info[i]["class"] == "SIGN_OFF":
            if lines_info[i]["class"] != "PLACEHOLDER":
                lines_info[i]["remove"] = True

    out = []
    for info in lines_info:
        if not info["remove"]:
            out.append(info["line"])
    return "\n".join(out)


# ═══════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════

def clean_email_body(raw: str, keep_history: bool = True) -> str:
    """
    Full 7-layer cleaning pipeline.
    Input:  raw email body (HTML or plain text).
    Output: clean plain-text string.
    """
    if not raw or not raw.strip():
        return ""

    # Layer 0+1: HTML path
    if _is_html(raw):
        raw = _strip_html(raw, keep_history=keep_history)
        text = _html_to_md(raw)
    else:
        text = raw

    # Common layers
    text = _normalise_unicode(text)
    text = _remove_escape_literals(text)
    text = _clean_links(text)

    # Stitch multi-line "On... wrote:" headers together
    text = re.sub(r"(^[\s>]*On\s+.{5,180})\n([\s>]*wrote\s*:)", r"\1 \2", text, flags=re.I | re.M)

    if not keep_history:
        text = _remove_reply_chain(text)          # Layer 2
    text = _remove_disclaimers(text)          # Layer 3
    text = _remove_signature(text, keep_history=keep_history)  # Layer 4a
    # pyrefly: ignore [unknown-name]
    text = _remove_line_noise(text)           # Layer 5

    block_info = {}
    reply_1_sender = ""

    if keep_history:
        # Extract header blocks and map to senders

        lines = text.split("\n")
        n = len(lines)
        
        is_header = [False] * n
        for i in range(n):
            s = lines[i].strip()
            if _REPLY_HEADER_PATTERN.match(s) or _ON_WROTE_PATTERN.match(s) or _FORWARDED_PATTERN.match(s):
                is_header[i] = True
                
        for i in range(1, n - 1):
            if not is_header[i] and lines[i].strip() == "":
                if any(is_header[j] for j in range(max(0, i-3), i)) and any(is_header[j] for j in range(i+1, min(n, i+4))):
                    is_header[i] = True

        header_blocks = []
        in_block = False
        block_start = -1
        for i in range(n):
            if is_header[i]:
                if not in_block:
                    in_block = True
                    block_start = i
            else:
                if in_block:
                    in_block = False
                    header_blocks.append((block_start, i))
        if in_block:
            header_blocks.append((block_start, n))

        for start, end in header_blocks:
            block_lines = lines[start:end]
            block_text = "\n".join(block_lines)
            
            prefix_match = re.match(r"^[\s>]*", block_lines[0])
            prefix = prefix_match.group(0) if prefix_match else ""
            depth = prefix.count('>')
            
            sender_email = ""
            recipient_email = ""
            
            for bline in block_lines:
                bline_clean = bline.strip()
                if re.search(r"\b(From|Von|De|Envoy\u00e9|Enviado)\b", bline_clean, re.I):
                    sender_email = _extract_email(bline_clean)
                    if not sender_email:
                        idx = block_lines.index(bline)
                        if idx + 1 < len(block_lines):
                            sender_email = _extract_email(block_lines[idx+1])
                elif re.search(r"\b(To|An|\u00c0|Para)\b", bline_clean, re.I):
                    recipient_email = _extract_email(bline_clean)
                    if not recipient_email:
                        idx = block_lines.index(bline)
                        if idx + 1 < len(block_lines):
                            recipient_email = _extract_email(block_lines[idx+1])
                    
            if not sender_email:
                sender_email = _extract_email(block_text)
                
            if recipient_email and not reply_1_sender and depth == 0:
                reply_1_sender = recipient_email
                
            block_info[start] = {
                "end": end,
                "sender": sender_email,
                "depth": depth
            }


        # Replace header blocks with placeholders
        new_lines = []
        i = 0
        while i < n:
            if i in block_info:
                new_lines.append(f"__REPLY_HEADER_PLACEHOLDER_{i}__")
                i = block_info[i]["end"]
            else:
                new_lines.append(lines[i])
                i += 1
        text = "\n".join(new_lines)

    if keep_history:
        text = _remove_inline_signatures(text)   # Layer 8
    text = _remove_trailing_names(text)       # Layer 4b (post-noise)

    # Strip all leading quote characters (e.g. >, >>) to return a clean flat text feed
    # Insert numbered separators to divide the chronological reply chain

    lines = text.split("\n")
    cleaned_lines = []
    
    current_depth = 0
    reply_counter = 1
    
    '''if keep_history:
        first_banner = "--- Reply 1"
        if reply_1_sender:
            first_banner += f" ({reply_1_sender})"
        first_banner += " ---"
        cleaned_lines.append(first_banner)
        cleaned_lines.append("")'''
        
    for idx, line in enumerate(lines):
        s_line = line.strip()
        
        if keep_history:
            placeholder_match = re.match(r"^__REPLY_HEADER_PLACEHOLDER_(\d+)__$", s_line)
            if placeholder_match:
                block_idx = int(placeholder_match.group(1))
                info = block_info[block_idx]
                sender = info["sender"]
                
                reply_counter += 1
                
                # Update current_depth to prevent double-triggering
                next_depth = info["depth"]
                for k in range(idx + 1, len(lines)):
                    next_s = lines[k].strip()
                    if not re.match(r"^__REPLY_HEADER_PLACEHOLDER_\d+__$", next_s) and next_s:
                        p_match = re.match(r"^[\s>]*", lines[k])
                        next_depth = p_match.group(0).count('>') if p_match else 0
                        break
                current_depth = next_depth
                continue
            
        prefix_match = re.match(r"^[\s>]*", line)
        prefix = prefix_match.group(0) if prefix_match else ""
        depth = prefix.count('>')
        
        cleaned_content = re.sub(r"^[\s>]*>[\s>]*", "", line)
        
        if keep_history and cleaned_content.strip():

            
            if depth > current_depth:
                reply_counter += 1
                current_depth = depth
            elif depth < current_depth:
                current_depth = depth
                
        cleaned_lines.append(cleaned_content)
        
    text = "\n".join(cleaned_lines)

    text = _collapse_whitespace(text)         # Layer 6

    text = text.strip('"').strip()

    # pyrefly: ignore [parse-error]
    return text if len(text) >= 10 else ""

# ═══════════════════════════════════════════════════════════════════
# BATCH PROCESSOR + REPORT
# ═══════════════════════════════════════════════════════════════════

def batch_clean(input_path: str, output_path=None, write_md: bool = True, keep_history: bool = True) -> dict:
    src = Path(input_path)
    if not src.exists():
        raise FileNotFoundError(src)

    print(f"Loading  : {src}  ({src.stat().st_size/1024:.1f} KB)")
    data = json.loads(src.read_text(encoding="utf-8"))
    total = len(data)
    print(f"Emails   : {total}")

    processed, empty = {}, 0
    for i, (eid, body) in enumerate(data.items(), 1):
        if i % 50 == 0 or i == total:
            print(f"  [{i}/{total}]", end="\r")
        result = clean_email_body(body, keep_history=keep_history)
        processed[eid] = result
        if not result:
            empty += 1

    print(f"\nDone     : {total-empty}/{total} non-empty  ({empty} skipped)")

    dst = Path(output_path) if output_path else src.with_name(src.stem + "_clean.json")
    dst.write_text(json.dumps(processed, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON     : {dst}  ({dst.stat().st_size/1024:.1f} KB)")

    if write_md:
        md_dst = dst.with_suffix(".md")
        _write_markdown(processed, md_dst, src.name)
        # Per-email individual markdown files
        md_folder = dst.with_name(dst.stem + "_emails")
        _write_individual_mds(processed, md_folder)

    return processed


def _sanitize_filename(eid: str) -> str:
    """Turn an email ID into a safe filename."""
    # Replace characters not allowed in filenames
    safe = re.sub(r'[<>:"/\\|?*@+]', '_', eid)
    # Truncate to 100 chars to avoid path-length issues on Windows
    if len(safe) > 100:
        safe = safe[:100]
    return safe


def _write_individual_mds(processed: dict, folder: Path) -> None:
    """Write one .md file per email into a dedicated folder."""
    folder.mkdir(parents=True, exist_ok=True)
    count = 0
    for eid, body in processed.items():
        if not body:
            continue
        fname = _sanitize_filename(eid) + ".md"
        fpath = folder / fname
        content = f"# Email: {eid}\n\n{body}\n"
        fpath.write_text(content, encoding="utf-8")
        count += 1
    print(f"Emails   : {count} files in {folder}")


def _write_markdown(processed: dict, path: Path, source: str = "") -> None:
    total = len(processed)
    non_empty = sum(1 for v in processed.values() if v)
    lengths = [len(v) for v in processed.values() if v]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "# Email Preprocessing Report\n",
        f"> **Source**: `{source}`  ",
        f"> **Generated**: {now}  ",
        f"> **Total**: {total}  |  **Non-empty**: {non_empty}  |  **Skipped**: {total-non_empty}  ",
        "",
    ]

    if lengths:
        lines += [
            "| Metric | Value |",
            "|--------|-------|",
            f"| Avg length | {int(sum(lengths)/len(lengths))} chars |",
            f"| Min length | {min(lengths)} chars |",
            f"| Max length | {max(lengths)} chars |",
            "",
        ]

    lines += ["---\n", "## Table of Contents\n"]
    for i, (eid, body) in enumerate(processed.items(), 1):
        short = eid[:55] + ("..." if len(eid) > 55 else "")
        flag = " *(empty)*" if not body else ""
        lines.append(f"{i}. [{short}](#email-{i}){flag}")
    lines.append("")

    lines.append("---\n")
    for i, (eid, body) in enumerate(processed.items(), 1):
        lines += [
            f'<a name="email-{i}"></a>',
            f"## Email {i}",
            f"> **ID**: `{eid}`",
            "",
            body if body else "*[empty after cleaning]*",
            "",
            "---\n",
        ]

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Markdown : {path}  ({path.stat().st_size/1024:.1f} KB)")


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Clean email bodies: remove disclaimers, signatures, and optionally reply chains."
    )
    parser.add_argument(
        "input", nargs="?",
        default=r"aviso_logic_monitor_email_eid_level.json",
        help="Path to input JSON file (format: {email_id: body, ...})",
    )
    parser.add_argument("-o", "--output", default=None)
    parser.add_argument("--no-md", action="store_true")
    parser.add_argument("--single", metavar="EMAIL_ID", default=None,
                        help="Clean and print a single email by ID.")
    parser.add_argument("--sample", type=int, default=0, metavar="N",
                        help="Print N sample results to console after processing.")
    parser.add_argument("--truncate", action="store_true",
                        help="Truncate the older reply chains and only keep the latest email body.")
    args = parser.parse_args()

    keep_history = not args.truncate

    if args.single:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if args.single not in data:
            print(f"ID not found: {args.single}")
            sys.exit(1)
        print(clean_email_body(data[args.single], keep_history=keep_history))
        return

    processed = batch_clean(args.input, args.output, write_md=not args.no_md, keep_history=keep_history)

    if args.sample > 0:
        print("\n" + "=" * 68)
        print(f"SAMPLE ({min(args.sample, len(processed))} emails)")
        print("=" * 68)
        for i, (eid, body) in enumerate(processed.items()):
            if i >= args.sample:
                break
            short = eid[:55] + "..." if len(eid) > 55 else eid
            print(f"\n[{i+1}] {short}")
            print(f"    Length: {len(body)} chars")
            safe = body[:500].encode("ascii", errors="replace").decode()
            print(safe)
            print("-" * 68)


if __name__ == "__main__":
    main()
