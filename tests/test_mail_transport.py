import ssl
from unittest.mock import MagicMock

import pytest

from app.services import mail_service


@pytest.fixture
def transports(monkeypatch):
    monkeypatch.setattr(mail_service, 'configured', lambda: True)
    for name, value in {'SMTP_HOST': 'smtp.example.test', 'SMTP_PORT': 465,
                        'SMTP_USER': 'notifications@example.test', 'SMTP_PASS': 'test-only',
                        'SMTP_FROM': 'Lernium <notifications@example.test>',
                        'SMTP_SSL': False, 'SMTP_STARTTLS': True}.items():
        monkeypatch.setattr(mail_service.settings, name, value)
    plain, encrypted = MagicMock(), MagicMock()
    monkeypatch.setattr(mail_service.smtplib, 'SMTP', plain)
    monkeypatch.setattr(mail_service.smtplib, 'SMTP_SSL', encrypted)
    return plain, encrypted


def test_465_uses_verified_tls_before_authentication(transports):
    plain, encrypted = transports
    mail_service.send_account_mail('recipient@example.test', 'Восстановление', 'Текст', 'stable-id')
    plain.assert_not_called()
    context = encrypted.call_args.kwargs['context']
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    smtp = encrypted.return_value.__enter__.return_value
    smtp.starttls.assert_not_called()
    smtp.login.assert_called_once()
    message = smtp.send_message.call_args.args[0]
    assert str(message['Subject']) == 'Восстановление'
    assert str(message['Message-ID']) == '<stable-id@lernium>'


def test_587_upgrades_before_authentication(transports, monkeypatch):
    plain, encrypted = transports
    monkeypatch.setattr(mail_service.settings, 'SMTP_PORT', 587)
    mail_service.send_account_mail('recipient@example.test', 'Test', 'Text')
    encrypted.assert_not_called()
    smtp = plain.return_value.__enter__.return_value
    assert [call[0] for call in smtp.method_calls] == ['starttls', 'login', 'send_message']
    assert smtp.starttls.call_args.kwargs['context'].check_hostname


def test_no_credentials_on_unencrypted_transport(transports, monkeypatch):
    monkeypatch.setattr(mail_service.settings, 'SMTP_PORT', 25)
    monkeypatch.setattr(mail_service.settings, 'SMTP_STARTTLS', False)
    with pytest.raises(RuntimeError, match='requires TLS'):
        mail_service.send_account_mail('recipient@example.test', 'Test', 'Text')
    for transport in transports:
        transport.assert_not_called()


def test_tls_error_is_not_downgraded(transports):
    plain, encrypted = transports
    encrypted.side_effect = ssl.SSLCertVerificationError('invalid certificate')
    with pytest.raises(ssl.SSLCertVerificationError):
        mail_service.send_account_mail('recipient@example.test', 'Test', 'Text')
    plain.assert_not_called()
