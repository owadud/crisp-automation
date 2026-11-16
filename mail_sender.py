"""Send campaign emails from your own mail account (Gmail, Outlook, Zoho or any SMTP server).

The account is saved only on this computer, in ~/.crisp_email_agent/mail_account.json.
Gmail needs an App password (Google Account > Security > 2-Step Verification > App passwords),
not your normal password.
"""

from __future__ import annotations

import html
import json
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

CONFIG = Path.home() / ".crisp_email_agent" / "mail_account.json"

# provider -> (SMTP host, port, security); "ssl" = SMTPS on 465, "starttls" = 587.
PROVIDERS = {
    "gmail": ("smtp.gmail.com", 465, "ssl"),
    "outlook": ("smtp.office365.com", 587, "starttls"),
    "zoho": ("smtp.zoho.com", 465, "ssl"),
    "other": ("", 587, "starttls"),
}
PROVIDER_LABELS = (("gmail", "Gmail / Google Workspace"), ("outlook", "Outlook / Microsoft 365"),
                   ("zoho", "Zoho Mail"), ("other", "Other (SMTP)"))
UNSUBSCRIBE_LINE = "Don't want these emails? Reply with \"unsubscribe\" and we'll take you off the list."


class MailError(RuntimeError):
    """uncertain = the email MAY have gone out (never retry it)."""

    def __init__(self, message: str, uncertain: bool = False):
        super().__init__(message)
        self.uncertain = uncertain


# ------------------------------------------------------------------ account file
def load_config(path: Path | None = None) -> dict:
    path = Path(path or CONFIG)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict, path: Path | None = None) -> Path:
    path = Path(path or CONFIG)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)
    return path


def remove_config(path: Path | None = None) -> None:
    try:
        Path(path or CONFIG).unlink()
    except OSError:
        pass


def configured(cfg: dict) -> bool:
    return bool(cfg.get("email") and cfg.get("password") and cfg.get("host"))


def problem(cfg: dict) -> str:
    """'' when the account is complete, else what is missing."""
    if not cfg.get("email"):
        return "Type your email address: Settings > Your email account."
    if not cfg.get("password"):
        return "Type the (app) password: Settings > Your email account."
    if not cfg.get("host"):
        return "Type the SMTP server: Settings > Your email account."
    return ""


# ------------------------------------------------------------------ message
def _inline(text: str) -> str:
    t = html.escape(text, quote=False)
    t = re.sub(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)", r'<img src="\2" alt="\1" style="max-width:100%">', t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+|mailto:[^)\s]+)\)", r'<a href="\2">\1</a>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)
    t = re.sub(r"~~(.+?)~~", r"<s>\1</s>", t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    return t


def to_html(markdown: str) -> str:
    """The editor's Markdown (bold, italic, links, lists, quotes) as simple email HTML."""
    out, para, items, kind = [], [], [], ""

    def flush():
        nonlocal para, items, kind
        if para:
            out.append("<p>" + "<br>".join(_inline(p) for p in para) + "</p>")
        if items:
            out.append(f"<{kind}>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + f"</{kind}>")
        para, items, kind = [], [], ""

    for line in str(markdown or "").splitlines():
        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        number = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if not line.strip():
            flush()
        elif bullet or number:
            want = "ul" if bullet else "ol"
            if para or (kind and kind != want):
                flush()
            kind = want
            items.append((bullet or number).group(1))
        elif line.startswith(">"):
            flush()
            out.append(f'<blockquote style="margin:0 0 1em;padding-left:12px;border-left:3px solid #ccc">'
                       f"{_inline(line.lstrip('> '))}</blockquote>")
        else:
            if items:
                flush()
            para.append(line)
    flush()
    return ('<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.5">'
            + "\n".join(out) + "</div>")


def to_plain(markdown: str) -> str:
    t = re.sub(r"!\[[^\]]*\]\(([^)\s]+)\)", r"\1", str(markdown or ""))
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r"\1 (\2)", t)
    t = re.sub(r"\*\*(.+?)\*\*|~~(.+?)~~|`([^`]+)`", lambda m: next(g for g in m.groups() if g), t)
    return t


def build_message(cfg: dict, to: str, subject: str, text: str, to_name: str = "") -> EmailMessage:
    sender = cfg.get("email", "")
    body = str(text or "").rstrip()
    if cfg.get("unsubscribe", True):
        body += "\n\n" + UNSUBSCRIBE_LINE
    msg = EmailMessage()
    msg["From"] = formataddr((cfg.get("from_name", "") or "", sender))
    msg["To"] = formataddr((to_name or "", to))
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2] or None)
    reply_to = (cfg.get("reply_to") or "").strip()
    if reply_to:
        msg["Reply-To"] = reply_to
    if cfg.get("unsubscribe", True):
        msg["List-Unsubscribe"] = f"<mailto:{reply_to or sender}?subject=unsubscribe>"
    msg.set_content(to_plain(body))
    msg.add_alternative(to_html(body), subtype="html")
    return msg


# ------------------------------------------------------------------ sending
class MailSender:
    """One SMTP connection kept open for the run, re-opened when the server drops it."""

    def __init__(self, cfg: dict, timeout: float = 30.0, smtp_factory=None):
        self.cfg = dict(cfg)
        self.timeout = timeout
        self.smtp_factory = smtp_factory       # tests pass a fake
        self.conn = None

    def _open(self):
        host = self.cfg.get("host", "")
        port = int(self.cfg.get("port") or 587)
        security = self.cfg.get("security") or "starttls"
        try:
            if self.smtp_factory:
                conn = self.smtp_factory(host, port, security)
            elif security == "ssl":
                conn = smtplib.SMTP_SSL(host, port, timeout=self.timeout, context=ssl.create_default_context())
            else:
                conn = smtplib.SMTP(host, port, timeout=self.timeout)
                conn.ehlo()
                if security == "starttls":
                    conn.starttls(context=ssl.create_default_context())
                    conn.ehlo()
            conn.login(self.cfg.get("email", ""), self.cfg.get("password", ""))
        except smtplib.SMTPAuthenticationError as exc:
            raise MailError("The mail server refused the email or password. For Gmail use an App password "
                            f"(not your normal password). Server said: {_said(exc)}") from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(f"Could not connect to {host}:{port} ({exc}). Check the server, port and security.") \
                from exc
        self.conn = conn
        return conn

    def connect(self) -> "MailSender":
        """Open and log in once (also the 'Test connection' check)."""
        if self.conn is None:
            self._open()
        return self

    def _alive(self) -> bool:
        try:
            return self.conn is not None and self.conn.noop()[0] == 250
        except (smtplib.SMTPException, OSError):
            return False

    def send(self, to: str, subject: str, text: str, to_name: str = "") -> str:
        """Send one email; returns its Message-ID. MailError(uncertain=True) = it MAY have gone out."""
        msg = build_message(self.cfg, to, subject, text, to_name)
        if not self._alive():
            self.close()
            self._open()                       # nothing was sent yet: safe to reconnect
        try:
            refused = self.conn.send_message(msg)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as exc:
            raise MailError(f"The mail server refused this address: {_said(exc)}") from exc
        except smtplib.SMTPDataError as exc:
            raise MailError(f"The mail server refused the email ({exc.smtp_code}): {_said(exc)}") from exc
        except (smtplib.SMTPException, OSError) as exc:
            self.close()
            raise MailError(f"The connection broke while sending ({exc}). The email MAY already have been sent.",
                            uncertain=True) from exc
        if refused:
            raise MailError(f"The mail server refused {', '.join(refused)}.")
        return msg["Message-ID"]

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.quit()
            except (smtplib.SMTPException, OSError):
                pass
        self.conn = None


def _said(exc) -> str:
    raw = getattr(exc, "smtp_error", None)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if raw is None and getattr(exc, "recipients", None):
        raw = "; ".join(f"{r}: {v[1].decode('utf-8', 'replace') if isinstance(v[1], bytes) else v[1]}"
                        for r, v in exc.recipients.items())
    return re.sub(r"\s+", " ", str(raw or exc)).strip()[:300]
