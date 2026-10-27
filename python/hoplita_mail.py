import email
import email.policy
import email.utils
import html as _h
import imaplib
import re
import socket
import ssl
from datetime import datetime
from html.parser import HTMLParser

TIMEOUT = 30
LIST_MAX = 100
SNIPPET = 240
READ_MAX_BYTES = 8 * 1024 * 1024

_HOSTS = {
    "gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
    "outlook.com": "outlook.office365.com", "hotmail.com": "outlook.office365.com",
    "live.com": "outlook.office365.com", "msn.com": "outlook.office365.com",
    "yahoo.com": "imap.mail.yahoo.com", "yahoo.es": "imap.mail.yahoo.com",
    "icloud.com": "imap.mail.me.com", "me.com": "imap.mail.me.com",
    "mac.com": "imap.mail.me.com",
}

_ROLES = {
    "junk": ("\\Junk", ("spam", "junk", "junk e-mail", "junk email", "bulk mail")),
    "trash": ("\\Trash", ("trash", "deleted items", "deleted messages", "bin")),
    "sent": ("\\Sent", ("sent", "sent items", "sent messages")),
    "drafts": ("\\Drafts", ("drafts",)),
    "archive": ("\\Archive", ("archive",)),
    "all": ("\\All", ()),
}
_ROLE_ALIASES = {"spam": "junk", "bin": "trash", "deleted": "trash"}


class MailError(Exception):
    pass



def _mutf7_decode(s):
    out, i = [], 0
    while i < len(s):
        if s[i] == "&":
            j = s.index("-", i)
            if j == i + 1:
                out.append("&")
            else:
                b64 = s[i + 1:j].replace(",", "/")
                b64 += "=" * (-len(b64) % 4)
                import base64
                out.append(base64.b64decode(b64).decode("utf-16-be"))
            i = j + 1
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _mutf7_encode(s):
    import base64
    out, buf = [], []

    def flush():
        if buf:
            b = base64.b64encode("".join(buf).encode("utf-16-be")).decode()
            out.append("&" + b.rstrip("=").replace("/", ",") + "-")
            buf.clear()
    for ch in s:
        if 0x20 <= ord(ch) <= 0x7e:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)


def _quote(name):
    enc = _mutf7_encode(name)
    return '"' + enc.replace("\\", "\\\\").replace('"', '\\"') + '"'



def server_of(cred):
    url = (cred.get("url") or "").strip()
    addr = (cred.get("username") or "").strip()
    starttls = False
    if url:
        m = re.match(r"^(imaps?)://", url, re.I)
        if m:
            starttls = m.group(1).lower() == "imap"
            url = url[m.end():]
        elif "://" in url:
            raise MailError(f"the server '{cred.get('url')}' is not imap:// or "
                            f"imaps://; give the IMAP host, e.g. imap.gmail.com")
        url = url.strip("/")
        host, _, port = url.partition(":")
        if port and not port.isdigit():
            raise MailError(f"bad port in '{cred.get('url')}'")
        return host, int(port) if port else (143 if starttls else 993), starttls
    if "@" not in addr:
        raise MailError("the credential has no IMAP server and its user is not "
                        "an address to derive one from")
    dom = addr.rsplit("@", 1)[1].lower()
    return _HOSTS.get(dom, f"imap.{dom}"), 993, False


def _auth_hint(host, err):
    msg = f"login refused by {host}: {err}"
    if "gmail" in host:
        msg += (". Gmail takes an APP PASSWORD here, not the account's password: "
                "turn on 2-Step Verification, then create one at "
                "https://myaccount.google.com/apppasswords")
    elif "office365" in host or "outlook" in host:
        msg += (". Microsoft accounts no longer accept a password over IMAP "
                "(they require OAuth), which HOPLITA does not do")
    return msg


class Mailbox:

    def __init__(self, cred):
        self.cred = cred
        self.host, self.port, self.starttls = server_of(cred)
        self.m = None
        self.caps = set()

    def __enter__(self):
        ctx = ssl.create_default_context()
        try:
            if self.starttls:
                self.m = imaplib.IMAP4(self.host, self.port, timeout=TIMEOUT)
                if "STARTTLS" not in self.m.capabilities:
                    raise MailError(f"{self.host}:{self.port} offers no STARTTLS; "
                                    f"refusing to send the password in clear")
                self.m.starttls(ssl_context=ctx)
            else:
                self.m = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ctx,
                                           timeout=TIMEOUT)
        except ssl.SSLCertVerificationError as e:
            raise MailError(f"{self.host}: the server's certificate does not "
                            f"verify ({e.verify_message})")
        except (OSError, imaplib.IMAP4.error) as e:
            if isinstance(e, MailError):
                raise
            raise MailError(f"cannot reach {self.host}:{self.port}: {e}")
        try:
            self.m.login(self.cred.get("username", ""), self.cred.get("password", ""))
        except imaplib.IMAP4.error as e:
            self._close()
            raise MailError(_auth_hint(self.host, _s(e.args[0] if e.args else e)))
        typ, dat = self.m.capability()
        self.caps = set(_s(dat[0]).upper().split()) if typ == "OK" and dat else set()
        return self

    def __exit__(self, *exc):
        self._close()

    def _close(self):
        if self.m is None:
            return
        try:
            if self.m.state == "SELECTED":
                self.m.close()
        except Exception:
            pass
        try:
            self.m.logout()
        except Exception:
            pass
        self.m = None

    def ok(self, res, what):
        typ, dat = res
        if typ != "OK":
            raise MailError(f"{what}: {_s(dat[0]) if dat else typ}")
        return dat

    def folders(self):
        dat = self.ok(self.m.list(), "LIST")
        out = []
        for line in dat:
            if isinstance(line, tuple):
                head, name = _s(line[0]), _s(line[1])
                flags = re.match(r"\(([^)]*)\)", head)
            else:
                line = _s(line)
                if not line:
                    continue
                mm = re.match(r'\(([^)]*)\)\s+(NIL|"(?:[^"\\]|\\.)*")\s+(.*)$', line)
                if not mm:
                    continue
                flags, name = mm, mm.group(3).strip()
                if name.startswith('"') and name.endswith('"'):
                    name = re.sub(r'\\(.)', r'\1', name[1:-1])
            fl = flags.group(1).split() if flags else []
            out.append({"name": _mutf7_decode(name), "flags": fl,
                        "selectable": "\\Noselect" not in fl
                                      and "\\NonExistent" not in fl})
        for f in out:
            f["role"] = _role_of(f, out)
        return out

    def resolve(self, folder):
        want = (folder or "").strip()
        if not want:
            raise MailError("no folder given")
        fs = self.folders()
        names = [f["name"] for f in fs if f["selectable"]]
        if want in names:
            return want
        role = _ROLE_ALIASES.get(want.lower(), want.lower())
        if role == "inbox":
            return "INBOX"
        for f in fs:
            if f["selectable"] and f["role"] == role:
                return f["name"]
        low = [n for n in names if n.lower() == want.lower()]
        if len(low) == 1:
            return low[0]
        if role in _ROLES:
            raise MailError(f"this server has no {role} folder; its folders: "
                            f"{', '.join(names)}")
        raise MailError(f"no folder '{want}'; the folders: {', '.join(names)}")

    def select(self, folder, readonly=True):
        typ, dat = self.m.select(_quote(folder), readonly=readonly)
        if typ != "OK":
            raise MailError(f"cannot open '{folder}': {_s(dat[0]) if dat else typ}")
        return int(_s(dat[0]) or 0)


def _role_of(f, all_folders):
    if f["name"].upper() == "INBOX":
        return "inbox"
    for role, (flag, _names) in _ROLES.items():
        if flag in f["flags"]:
            return role
    leaf = f["name"].rsplit("/", 1)[-1].rsplit(".", 1)[-1].lower()
    for role, (flag, names) in _ROLES.items():
        if leaf in names and not any(flag in o["flags"] for o in all_folders):
            return role
    return ""



def _s(b):
    if isinstance(b, bytes):
        return b.decode("utf-8", "replace")
    return "" if b is None else str(b)


_LIT_RE = re.compile(rb"(BODY\[[^\]]*\](?:<\d+>)?|RFC822(?:\.HEADER|\.TEXT)?)\s*\{\d+\}$", re.I)


def _fetch_groups(dat):
    groups, cur = [], None
    for item in dat:
        if isinstance(item, tuple):
            head = item[0]
            if re.match(rb"^\d+ \(", head):
                cur = {"meta": "", "parts": {}}
                groups.append(cur)
            if cur is None:
                continue
            m = _LIT_RE.search(head)
            key = m.group(1).decode().upper() if m else "?"
            cur["parts"][key] = item[1]
            cur["meta"] += " " + _s(head[:m.start()] if m else head)
        elif isinstance(item, bytes):
            if re.match(rb"^\d+ \(", item):
                cur = {"meta": "", "parts": {}}
                groups.append(cur)
            if cur is not None:
                cur["meta"] += " " + _s(item)
    return groups


def _meta(meta):
    uid = re.search(r"\bUID (\d+)", meta)
    flags = re.search(r"\bFLAGS \(([^)]*)\)", meta)
    size = re.search(r"\bRFC822\.SIZE (\d+)", meta)
    idate = re.search(r'\bINTERNALDATE "([^"]+)"', meta)
    return {"uid": int(uid.group(1)) if uid else None,
            "flags": flags.group(1).split() if flags else [],
            "size": int(size.group(1)) if size else None,
            "internaldate": idate.group(1) if idate else ""}


class _Text(HTMLParser):
    _SKIP = {"script", "style", "head", "title"}
    _BREAK = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "table", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self.skip += 1
        elif tag in self._BREAK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self._BREAK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_text(html):
    p = _Text()
    try:
        p.feed(html)
        p.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", _h.unescape(html))
    txt = "".join(p.out)
    txt = re.sub(r"[ \t\r\f\v ]+", " ", txt)
    return re.sub(r"\n\s*\n+", "\n\n", txt).strip()


def _part_text(part):
    try:
        return part.get_content()
    except Exception:
        raw = part.get_payload(decode=True) or b""
        return raw.decode(part.get_content_charset() or "utf-8", "replace")


def _body(msg):
    plain = htmls = None
    atts = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = (part.get_content_disposition() or "").lower()
        ctype = part.get_content_type()
        fname = part.get_filename()
        if disp == "attachment" or (fname and disp != "inline") or \
                not ctype.startswith("text/"):
            if fname or disp == "attachment":
                payload = part.get_payload(decode=True) or b""
                atts.append({"name": fname or "", "type": ctype, "size": len(payload)})
            continue
        if ctype == "text/plain" and plain is None:
            plain = _part_text(part)
        elif ctype == "text/html" and htmls is None:
            htmls = _part_text(part)
    if plain and plain.strip():
        return plain.strip(), False, atts
    if htmls:
        return html_text(htmls), True, atts
    return "", False, atts


def _addr(v):
    return str(v or "").strip()


def _date(msg, internaldate=""):
    try:
        d = email.utils.parsedate_to_datetime(str(msg.get("Date", "")))
        return d.strftime("%Y-%m-%d %H:%M %z").strip()
    except Exception:
        return internaldate


def _snippet_message(header, text):
    raw = header.rstrip(b"\r\n") + b"\r\n\r\n" + (text or b"")
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    return msg


def _snippet(msg):
    try:
        txt, _html_only, _a = _body(msg)
    except Exception:
        txt = ""
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt[:SNIPPET] + ("…" if len(txt) > SNIPPET else "")



def folders(cred):
    with Mailbox(cred) as mb:
        out = []
        for f in mb.folders():
            row = {"name": f["name"], "role": f["role"]}
            if f["selectable"]:
                typ, dat = mb.m.status(_quote(f["name"]), "(MESSAGES UNSEEN)")
                if typ == "OK" and dat:
                    st = _s(dat[0])
                    for k in ("MESSAGES", "UNSEEN"):
                        mm = re.search(rf"{k} (\d+)", st)
                        if mm:
                            row[k.lower()] = int(mm.group(1))
            else:
                row["selectable"] = False
            out.append(row)
        return {"account": cred.get("site"), "folders": out}


def _search_args(unseen, since, before, from_, subject, text, to):
    crit, lit = [], None
    if unseen:
        crit.append("UNSEEN")
    for key, val in (("SINCE", since), ("BEFORE", before)):
        if val:
            try:
                d = datetime.strptime(val, "%Y-%m-%d")
            except ValueError:
                raise MailError(f"{key.lower()} must be YYYY-MM-DD; got '{val}'")
            crit += [key, d.strftime("%d-%b-%Y")]
    lit_key = None
    for key, val in (("FROM", from_), ("TO", to), ("SUBJECT", subject), ("TEXT", text)):
        if not val:
            continue
        if all(ord(c) < 128 for c in val):
            crit += [key, '"' + val.replace("\\", "\\\\").replace('"', '\\"') + '"']
        elif lit is None:
            lit_key, lit = key, val.encode("utf-8")
        else:
            raise MailError("one search term with accents or non-Latin letters "
                            "per call; the others must be plain ASCII")
    if lit is not None:
        crit.append(lit_key)
    return crit or ["ALL"], lit


def list_messages(cred, folder="INBOX", limit=25, unseen=False, since=None,
                  before=None, from_=None, subject=None, text=None, to=None):
    try:
        limit = int(limit or 25)
    except (TypeError, ValueError):
        raise MailError(f"limit must be a number; got '{limit}'")
    limit = max(1, min(limit, LIST_MAX))
    crit, lit = _search_args(unseen, since, before, from_, subject, text, to)
    with Mailbox(cred) as mb:
        name = mb.resolve(folder or "INBOX")
        total = mb.select(name, readonly=True)
        if lit is not None:
            mb.m.literal = lit
            dat = mb.ok(mb.m.uid("SEARCH", "CHARSET", "UTF-8", *crit), "SEARCH")
        else:
            dat = mb.ok(mb.m.uid("SEARCH", *crit), "SEARCH")
        uids = sorted({int(x) for x in _s(dat[0]).split() if x.isdigit()}, reverse=True)
        matched = len(uids)
        uids = uids[:limit]
        msgs = []
        if uids:
            fields = "FROM TO SUBJECT DATE LIST-UNSUBSCRIBE CONTENT-TYPE CONTENT-TRANSFER-ENCODING MIME-VERSION"
            dat = mb.ok(mb.m.uid(
                "FETCH", ",".join(map(str, uids)),
                f"(UID FLAGS INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER.FIELDS ({fields})] "
                f"BODY.PEEK[TEXT]<0.4096>)"), "FETCH")
            by_uid = {}
            for g in _fetch_groups(dat):
                meta = _meta(g["meta"])
                if meta["uid"] is None:
                    continue
                hdr = next((v for k, v in g["parts"].items() if "HEADER" in k), b"")
                body = next((v for k, v in g["parts"].items() if "TEXT" in k), b"")
                msg = _snippet_message(hdr, body)
                row = {"uid": meta["uid"], "from": _addr(msg.get("From")),
                       "subject": str(msg.get("Subject", "") or ""),
                       "date": _date(msg, meta["internaldate"]),
                       "seen": "\\Seen" in meta["flags"],
                       "snippet": _snippet(msg)}
                if "\\Flagged" in meta["flags"]:
                    row["flagged"] = True
                if msg.get("List-Unsubscribe"):
                    row["list_unsubscribe"] = True
                if meta["size"] is not None:
                    row["size"] = meta["size"]
                by_uid[meta["uid"]] = row
            msgs = [by_uid[u] for u in uids if u in by_uid]
        return {"account": cred.get("site"), "folder": name, "total": total,
                "matched": matched, "shown": len(msgs), "messages": msgs}


def read_message(cred, uid, folder="INBOX", max_chars=20000):
    try:
        uid = int(uid)
    except (TypeError, ValueError):
        raise MailError(f"uid must be the number mail_list gives; got '{uid}'")
    try:
        max_chars = max(500, int(max_chars or 20000))
    except (TypeError, ValueError):
        raise MailError(f"max_chars must be a number; got '{max_chars}'")
    with Mailbox(cred) as mb:
        name = mb.resolve(folder or "INBOX")
        mb.select(name, readonly=True)
        dat = mb.ok(mb.m.uid("FETCH", str(uid), "(UID RFC822.SIZE)"), "FETCH")
        g = [x for x in _fetch_groups(dat) if _meta(x["meta"])["uid"] == uid]
        if not g:
            raise MailError(f"no message with uid {uid} in '{name}' (it may have "
                            f"been moved: list the folder again)")
        size = _meta(g[0]["meta"])["size"] or 0
        section = "BODY.PEEK[]" if size <= READ_MAX_BYTES else f"BODY.PEEK[]<0.{READ_MAX_BYTES}>"
        dat = mb.ok(mb.m.uid("FETCH", str(uid), f"(UID FLAGS {section})"), "FETCH")
        g = _fetch_groups(dat)[0]
        meta = _meta(g["meta"])
        raw = next(iter(g["parts"].values()), b"")
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    text, html_only, atts = _body(msg)
    out = {"account": cred.get("site"), "folder": name, "uid": uid,
           "from": _addr(msg.get("From")), "to": _addr(msg.get("To")),
           "subject": str(msg.get("Subject", "") or ""),
           "date": _date(msg), "seen": "\\Seen" in meta["flags"],
           "text": text[:max_chars]}
    if msg.get("Cc"):
        out["cc"] = _addr(msg.get("Cc"))
    if msg.get("Reply-To"):
        out["reply_to"] = _addr(msg.get("Reply-To"))
    if msg.get("List-Unsubscribe"):
        out["list_unsubscribe"] = str(msg.get("List-Unsubscribe"))
    if html_only:
        out["html_only"] = True
    if len(text) > max_chars:
        out["text_truncated"] = len(text)
    if atts:
        out["attachments"] = atts
    if size > READ_MAX_BYTES:
        out["message_truncated"] = size
    return out


def _uid_set(s):
    out = []
    for part in (s or "").split(","):
        if ":" in part:
            a, b = part.split(":")
            out += list(range(int(a), int(b) + 1))
        elif part.isdigit():
            out.append(int(part))
    return out


def _copyuid(texts):
    for t in texts:
        m = re.search(r"COPYUID \d+ ([\d:,]+) ([\d:,]+)", _s(t))
        if m:
            return dict(zip(_uid_set(m.group(1)), _uid_set(m.group(2))))
    return {}


def move_messages(cred, uids, to, folder="INBOX"):
    if isinstance(uids, (int, str)):
        uids = [uids]
    try:
        uids = sorted({int(u) for u in (uids or [])})
    except (TypeError, ValueError):
        raise MailError(f"uids must be the numbers mail_list gives; got {uids!r}")
    if not uids:
        raise MailError("no uids given")
    if not (to or "").strip():
        raise MailError("no destination: give a folder or a role "
                        "('junk', 'trash', 'inbox', 'archive')")
    with Mailbox(cred) as mb:
        src = mb.resolve(folder or "INBOX")
        dst = mb.resolve(to)
        if src == dst:
            raise MailError(f"the messages are already in '{src}'")
        mb.select(src, readonly=False)
        dat = mb.ok(mb.m.uid("SEARCH", "UID", ",".join(map(str, uids))), "SEARCH")
        present = sorted({int(x) for x in _s(dat[0]).split() if x.isdigit()} & set(uids))
        missing = [u for u in uids if u not in present]
        mapping = {}
        if present:
            uset = ",".join(map(str, present))
            if "MOVE" in mb.caps or "IMAP4REV2" in mb.caps:
                mb.m.untagged_responses.pop("COPYUID", None)
                typ, dat = mb.m.uid("MOVE", uset, _quote(dst))
                if typ != "OK":
                    raise MailError(f"MOVE to '{dst}': {_s(dat[0]) if dat else typ}")
                codes = [x for x in mb.m.untagged_responses.get("COPYUID", [])]
                mapping = _copyuid([b"COPYUID " + c if isinstance(c, bytes) else c
                                    for c in codes] + list(dat or []))
            elif "UIDPLUS" in mb.caps:
                dat = mb.ok(mb.m.uid("COPY", uset, _quote(dst)), f"COPY to '{dst}'")
                mapping = _copyuid(dat)
                mb.ok(mb.m.uid("STORE", uset, "+FLAGS.SILENT", "(\\Deleted)"), "STORE")
                mb.ok(mb.m.uid("EXPUNGE", uset), "UID EXPUNGE")
            else:
                raise MailError(f"{mb.host} supports neither MOVE nor UIDPLUS: "
                                f"messages cannot be moved safely there")
    out = {"account": cred.get("site"), "moved": len(present), "from": src, "to": dst}
    if mapping:
        out["new_uids"] = [mapping[u] for u in present if u in mapping]
    if missing:
        out["not_found"] = missing
    return out
