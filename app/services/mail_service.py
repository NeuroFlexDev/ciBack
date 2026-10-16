"""Transactional account mail. Tokens are delivered only to the verified target address."""
import smtplib
from email.message import EmailMessage
from app.core.config import settings


def configured():
    return bool(settings.SMTP_HOST and settings.SMTP_FROM)


def send_account_mail(recipient: str, subject: str, text: str):
    if not configured():
        raise RuntimeError("Account mail is not configured")
    message = EmailMessage()
    message["From"] = settings.SMTP_FROM
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text)
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
        if settings.SMTP_STARTTLS:
            smtp.starttls()
        if settings.SMTP_USER:
            smtp.login(settings.SMTP_USER, settings.SMTP_PASS)
        smtp.send_message(message)
