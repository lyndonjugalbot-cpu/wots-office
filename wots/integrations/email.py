"""Email for outreach (spec v2 §8, §11, Phase 4): sent from the office's OWN mailbox, never from
platform infrastructure. Most transactional email services (SendGrid, Postmark, Mailgun...)
forbid cold outreach in their terms, so we use the office's mailbox over SMTP, and read replies
and opt-outs from it over IMAP.

Dry-run mode sends nothing: messages are written as .eml files to data/outbox/{org}/emails/.
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import re
import smtplib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid, parseaddr
from pathlib import Path

from .errors import IntegrationConfigError, IntegrationError

OPT_OUT = re.compile(r"\b(unsubscribe|remove me|opt[ -]?out|stop (emailing|contacting)|do not (contact|email)|"
                     r"don'?t (contact|email)|not interested|no thanks|take me off)\b", re.I)
AUTO_REPLY = re.compile(r"^(out of (the )?office|automatic reply|auto[- ]?reply|autoreply|away from)", re.I)
BOUNCE_FROM = re.compile(r"(mailer-daemon|postmaster)@", re.I)
BOUNCE_SUBJECT = re.compile(r"(undeliverable|delivery status notification|mail delivery failed|returned mail)", re.I)


@dataclass
class OutgoingEmail:
    to: str
    subject: str
    body: str
    from_addr: str  # "Lyndon at Wots Office <lyndon@example.com>"
    attachments: list[tuple[str, bytes, str]] = field(default_factory=list)  # (filename, data, mime type)
    in_reply_to: str | None = None  # a follow-up threads under the pitch

    def message(self) -> EmailMessage:
        msg = EmailMessage(policy=email.policy.SMTP)
        address = parseaddr(self.from_addr)[1]
        msg["From"] = self.from_addr
        msg["To"] = self.to
        msg["Subject"] = self.subject
        msg["Date"] = format_datetime(datetime.now(timezone.utc))
        msg["Message-ID"] = make_msgid(domain=address.split("@")[-1] or "localhost")
        # One-click-style opt-out that mail apps show as an "Unsubscribe" button (a reply, for now)
        msg["List-Unsubscribe"] = f"<mailto:{address}?subject=unsubscribe>"
        if self.in_reply_to:
            msg["In-Reply-To"] = self.in_reply_to
            msg["References"] = self.in_reply_to
        msg.set_content(self.body)
        for name, data, mime in self.attachments:
            main, sub = mime.split("/", 1)
            msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
        return msg


class OutboxSender:
    """Dry run: saves each message as an .eml file instead of sending it."""
    live = False

    def __init__(self, folder: Path):
        self.folder = folder

    def send(self, mail: OutgoingEmail) -> str:
        msg = mail.message()
        self.folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        (self.folder / f"{stamp}.eml").write_bytes(bytes(msg))
        return msg["Message-ID"]


class SmtpSender:
    live = True

    def __init__(self, host: str | None, port: int, username: str | None, password: str | None, timeout: float = 30):
        if not (host and username and password):
            raise IntegrationConfigError("The office mailbox isn't set up: add SMTP_HOST, SMTP_USERNAME, SMTP_PASSWORD "
                                         "and OUTREACH_FROM to .env")
        self.host, self.port, self.username, self.password, self.timeout = host, port, username, password, timeout

    def send(self, mail: OutgoingEmail) -> str:
        msg = mail.message()
        try:
            if self.port == 465:
                server = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
                server.starttls()
            with server:
                server.login(self.username, self.password)
                server.send_message(msg)
        except smtplib.SMTPAuthenticationError as e:
            raise IntegrationConfigError(f"The mailbox refused the login ({e.smtp_code})") from e
        except smtplib.SMTPRecipientsRefused as e:
            raise IntegrationError(f"The recipient was refused: {mail.to}") from e
        except (smtplib.SMTPException, OSError) as e:
            raise IntegrationError(f"Couldn't send through {self.host}: {e}") from e
        return msg["Message-ID"]


@dataclass
class IncomingEmail:
    from_addr: str
    subject: str
    text: str
    message_id: str | None = None
    in_reply_to: str | None = None
    auto_submitted: bool = False

    @property
    def kind(self) -> str:
        """opt_out | bounce | auto | reply"""
        if BOUNCE_FROM.search(self.from_addr) or BOUNCE_SUBJECT.search(self.subject):
            return "bounce"
        if self.auto_submitted or AUTO_REPLY.search(self.subject.strip()):
            return "auto"
        first_lines = "\n".join(line for line in self.text.splitlines() if not line.startswith(">"))[:1500]
        if OPT_OUT.search(self.subject) or OPT_OUT.search(first_lines):
            return "opt_out"
        return "reply"


def parse_incoming(raw: bytes) -> IncomingEmail:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else ""
    if body is not None and body.get_content_type() == "text/html":
        text = re.sub(r"<[^>]+>", " ", text)
    auto = (msg.get("Auto-Submitted", "no").lower() != "no") or bool(msg.get("X-Autoreply"))
    return IncomingEmail(from_addr=parseaddr(msg.get("From", ""))[1].lower(), subject=msg.get("Subject", ""),
                         text=text, message_id=msg.get("Message-ID"), in_reply_to=msg.get("In-Reply-To"),
                         auto_submitted=auto)


class ImapReader:
    def __init__(self, host: str, port: int, username: str, password: str):
        self.host, self.port, self.username, self.password = host, port, username, password

    def since(self, day: date) -> list[IncomingEmail]:
        """Messages in the inbox received on or after `day` (read-only: nothing is marked or moved)."""
        try:
            with imaplib.IMAP4_SSL(self.host, self.port) as box:
                box.login(self.username, self.password)
                box.select("INBOX", readonly=True)
                _, found = box.search(None, "SINCE", day.strftime("%d-%b-%Y"))
                out = []
                for num in (found[0] or b"").split():
                    _, data = box.fetch(num, "(BODY.PEEK[])")
                    if data and isinstance(data[0], tuple):
                        out.append(parse_incoming(data[0][1]))
                return out
        except imaplib.IMAP4.error as e:
            if "auth" in str(e).lower() or "login" in str(e).lower():
                raise IntegrationConfigError(f"The mailbox refused the login: {e}") from e
            raise IntegrationError(f"Couldn't read the mailbox: {e}") from e
        except OSError as e:
            raise IntegrationError(f"Couldn't reach {self.host}: {e}") from e
