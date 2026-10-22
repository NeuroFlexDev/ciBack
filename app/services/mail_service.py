"""Transactional account mail. Tokens are delivered only to the verified target address."""
import smtplib
import ssl
import uuid
from datetime import datetime, timedelta
from email.message import EmailMessage
from app.core.config import settings
from app.models.integrations import OutboundMessage
from app.services.integration_secrets import seal


def configured():
    from app.core.integration_config import integration_settings
    return bool(settings.SMTP_HOST and settings.SMTP_FROM and integration_settings.DATA_ENCRYPTION_KEY.get_secret_value())


def send_account_mail(recipient: str, subject: str, text: str, message_id: str | None = None):
    if not configured():
        raise RuntimeError("Account mail is not configured")
    message = EmailMessage()
    message["From"] = settings.SMTP_FROM
    message["To"] = recipient
    message["Subject"] = subject
    message["Message-ID"] = f"<{message_id or uuid.uuid4()}@lernium>"
    message.set_content(text)
    # Port 465 starts TLS before the SMTP greeting (Yandex and other implicit-TLS providers).
    implicit_tls = settings.SMTP_SSL or settings.SMTP_PORT == 465
    if settings.SMTP_USER and not (implicit_tls or settings.SMTP_STARTTLS):
        raise RuntimeError("SMTP authentication requires TLS")
    transport = smtplib.SMTP_SSL if implicit_tls else smtplib.SMTP
    options = {"context": ssl.create_default_context()} if implicit_tls else {}
    with transport(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15, **options) as smtp:
        if not implicit_tls and settings.SMTP_STARTTLS:
            smtp.starttls(context=ssl.create_default_context())
        if settings.SMTP_USER:
            smtp.login(settings.SMTP_USER, settings.SMTP_PASS)
        smtp.send_message(message)


def queue_mail(db, recipient, subject, text, *, expires_at=None, deduplication_key=None, organization_id=None):
    """Add within the caller's transaction. The durable worker owns delivery and retries."""
    if not configured():
        raise RuntimeError("Account mail is not configured")
    message_id = str(uuid.uuid4())
    row = OutboundMessage(id=message_id, kind="mail", organization_id=organization_id,
        deduplication_key=deduplication_key or f"mail:{message_id}",
        payload_encrypted=seal({"recipient": recipient, "subject": subject, "text": text}),
        available_at=datetime.utcnow(), expires_at=expires_at)
    db.add(row)
    return row


def queue_reset(db, recipient, url, token_hash, locale="ru"):
    if locale == "en":
        subject = "Lernium — reset your password"
        text = f"Open this link within 30 minutes to choose a new password:\n{url}\nIf you did not request this, ignore this email."
    else:
        subject = "Лерниум — восстановление доступа"
        text = f"Чтобы задать новый пароль, откройте ссылку в течение 30 минут:\n{url}\nЕсли вы не запрашивали смену пароля, проигнорируйте письмо."
    return queue_mail(db, recipient, subject, text, expires_at=datetime.utcnow()+timedelta(minutes=30),
                      deduplication_key=f"reset:{token_hash}")


def queue_invitation(db, recipient, url, organization_name, locale="ru"):
    if locale == "en":
        subject = f"Lernium — invitation to {organization_name}"
        text = f"You have been invited to {organization_name}. Open this link within 7 days:\n{url}\nIf you were not expecting an invitation, contact your administrator."
    else:
        subject = f"Лерниум — приглашение в {organization_name}"
        text = f"Вас пригласили в организацию «{organization_name}». Откройте ссылку в течение 7 дней:\n{url}\nЕсли вы не ожидали приглашения, свяжитесь с администратором."
    return queue_mail(db, recipient, subject, text, expires_at=datetime.utcnow()+timedelta(days=7))
