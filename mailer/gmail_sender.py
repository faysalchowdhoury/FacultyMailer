import smtplib
import os
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.application import MIMEApplication
from email.header import Header
from email.utils import formataddr


def _clean(text: str) -> str:
    """
    Normalize scraped text so it can go into an email safely.
    The CMU directory (and many others) is full of non-breaking spaces
    (\xa0) and similar characters that crash naive ASCII header encoding.
    """
    if not text:
        return ""
    for ch in ("\xa0", "\u2007", "\u202f", "\u200b"):
        text = text.replace(ch, " ")
    return text


class GmailSender:
    """
    Gmail SMTP sender that survives long runs.

    Gmail closes a connection after it has been open a while or after many
    messages, returning "421 4.7.0 Connection expired". The old code then hit
    "please run connect() first" on every remaining recipient, so one drop
    cascaded into dozens of failures. This version:
      * checks the connection is alive before each send (cheap NOOP),
      * transparently reconnects and retries once when the socket drops,
      * proactively reconnects every `reconnect_every` messages so it rarely
        gets to the point where Gmail forces a drop.
    A genuinely bad recipient (address refused) is NOT retried -- it's raised
    so the caller can log just that one and move on.
    """

    def __init__(self, email_address: str, app_password: str, delay: float = 0.0,
                 reconnect_every: int = 40):
        self.email_address = email_address
        self.app_password = app_password
        self.delay = delay
        self.reconnect_every = max(0, int(reconnect_every))
        self._server = None
        self._sent_since_connect = 0

    # -- connection management ------------------------------------------------
    def _connect(self):
        self._close_quietly()
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60)
        server.login(self.email_address, self.app_password)
        self._server = server
        self._sent_since_connect = 0

    def _close_quietly(self):
        if self._server is not None:
            try:
                self._server.quit()
            except Exception:
                try:
                    self._server.close()
                except Exception:
                    pass
        self._server = None

    def ensure_connected(self):
        """Make sure we have a live connection; reconnect if the socket died
        or if we've sent enough messages to be near Gmail's limit."""
        if self._server is None:
            self._connect()
            return
        if self.reconnect_every and self._sent_since_connect >= self.reconnect_every:
            self._connect()
            return
        try:
            status = self._server.noop()[0]
            if status != 250:
                self._connect()
        except Exception:
            self._connect()

    def __enter__(self):
        self._connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._close_quietly()

    # -- message building -----------------------------------------------------
    def _build_message(self, recipient, subject, html_body,
                       attachment_path=None, attachment_name=None,
                       inline_images=None):
        msg = MIMEMultipart("related")
        msg["From"] = formataddr((str(Header(self.email_address, "utf-8")), self.email_address))
        msg["To"] = recipient
        msg["Subject"] = Header(_clean(subject), "utf-8")

        msg.attach(MIMEText(_clean(html_body), "html", "utf-8"))

        for img in (inline_images or []):
            part = MIMEImage(img["data"])
            cid = img["cid"]
            part.add_header("Content-ID", f"<{cid}>")
            part.add_header("Content-Disposition", "inline", filename=f"{cid}.png")
            msg.attach(part)

        if attachment_path and os.path.exists(attachment_path):
            display_name = attachment_name or os.path.basename(attachment_path)
            with open(attachment_path, "rb") as f:
                part = MIMEApplication(f.read(), Name=display_name)
            part["Content-Disposition"] = f'attachment; filename="{display_name}"'
            msg.attach(part)

        return msg

    # -- sending --------------------------------------------------------------
    def _deliver_with_retry(self, msg, attempts: int = 2):
        last_exc = None
        for _ in range(max(1, attempts)):
            try:
                self.ensure_connected()
                self._server.send_message(msg)
                self._sent_since_connect += 1
                return
            except smtplib.SMTPRecipientsRefused:
                # Bad address for THIS recipient -- permanent, don't retry.
                raise
            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
                    smtplib.SMTPResponseException, smtplib.SMTPSenderRefused,
                    OSError, ConnectionError) as e:
                # Connection-level problem (incl. 421 Connection expired):
                # drop the socket and try again with a fresh one.
                last_exc = e
                self._close_quietly()
                continue
        raise last_exc if last_exc else RuntimeError("send failed")

    def send_email(self, recipient: str, subject: str, html_body: str,
                   attachment_path: str = None, attachment_name: str = None,
                   inline_images=None):
        """Send one email, reconnecting transparently if Gmail drops us."""
        msg = self._build_message(recipient, subject, html_body,
                                  attachment_path, attachment_name, inline_images)

        if self._server is not None:
            self._deliver_with_retry(msg)
        else:
            # one-off (not used as a context manager)
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as server:
                server.login(self.email_address, self.app_password)
                server.send_message(msg)

        if self.delay:
            time.sleep(self.delay)
