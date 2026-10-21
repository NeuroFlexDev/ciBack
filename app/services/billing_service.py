import base64
from datetime import datetime
from decimal import Decimal, InvalidOperation
from fastapi import HTTPException
from app.core.integration_config import integration_settings
from app.models.integrations import BillingAccount, Invoice, PaymentEvent
from app.services import integration_http
from app.services.organization_access import audit
from app.services.outbox_service import enqueue_event


def ensure_write_access(db,organization_id):
    """Contract expiry stops new work while reads, export and renewal retain all historical data."""
    if not organization_id:return
    account=db.get(BillingAccount,organization_id)
    if account and account.status=="pending":
        raise HTTPException(403,"Договор ожидает подтверждения оплаты или согласования доступа оператором. Данные доступны для просмотра и выгрузки.")
    if account and (account.status in {"pending","suspended","cancelled"} or (account.status=="active" and account.access_until and account.access_until<=datetime.utcnow())):
        raise HTTPException(403,"Срок доступа по договору завершён. Данные сохранены и доступны для просмотра и выгрузки; обратитесь к владельцу организации для продления.")


def invoice_dict(row):
    return {key:getattr(row,key) for key in ("id","number","amount_minor","currency","description","status","due_at","service_until","paid_at","payment_reference","created_at")}


def apply_payment(db, invoice, *, reference, provider, actor=None, reason=""):
    if invoice.status == "paid":
        if invoice.payment_reference != reference:
            raise HTTPException(409,"Счёт уже оплачен другим платежом")
        return False
    if invoice.status != "issued":
        raise HTTPException(409,"Этот счёт нельзя оплатить")
    duplicate=db.query(Invoice.id).filter(Invoice.payment_reference==reference,Invoice.id!=invoice.id).first()
    if duplicate:raise HTTPException(409,"Платёж уже связан с другим счётом")
    invoice.status="paid";invoice.paid_at=datetime.utcnow();invoice.payment_reference=reference
    account=db.query(BillingAccount).filter_by(organization_id=invoice.organization_id).with_for_update().one()
    account.status="active"
    account.access_until=max(account.access_until,invoice.service_until) if account.access_until else invoice.service_until
    db.add(PaymentEvent(id=reference,invoice_id=invoice.id,provider=provider,status="succeeded",amount_minor=invoice.amount_minor))
    audit(db,actor,"invoice.paid","invoice",invoice.id,organization_id=invoice.organization_id,provider=provider,reason=reason)
    enqueue_event(db,invoice.organization_id,"invoice.paid",reference,{"invoice_id":invoice.id,"amount_minor":invoice.amount_minor,"currency":invoice.currency})
    return True


def fetch_verified_payment(payment_id):
    settings=integration_settings
    if not settings.YOOKASSA_SHOP_ID or not settings.YOOKASSA_SECRET_KEY.get_secret_value():
        raise HTTPException(503,"Платёжный провайдер не настроен")
    authorization=base64.b64encode(f"{settings.YOOKASSA_SHOP_ID}:{settings.YOOKASSA_SECRET_KEY.get_secret_value()}".encode()).decode()
    try:
        return integration_http.request_json("GET",f"https://api.yookassa.ru/v3/payments/{payment_id}",headers={"Authorization":"Basic "+authorization})
    except Exception:
        raise HTTPException(503,"Не удалось проверить платёж у провайдера")


def reconcile_payment(db,payment_id):
    payment=fetch_verified_payment(payment_id)
    if payment.get("id")!=payment_id or payment.get("status")!="succeeded" or payment.get("paid") is not True:
        raise HTTPException(409,"Провайдер ещё не подтвердил оплату")
    # Test payments never enable a real production contract.
    if payment.get("test") is True:
        raise HTTPException(409,"Тестовый платёж не активирует договор")
    invoice_id=payment.get("metadata",{}).get("invoice_id")
    invoice=db.query(Invoice).filter_by(id=invoice_id).with_for_update().first()
    if not invoice:raise HTTPException(404,"Счёт для платежа не найден")
    try:
        amount=Decimal(payment["amount"]["value"])*100
        if not amount.is_finite() or amount!=amount.to_integral_value():raise ValueError()
        amount=int(amount)
    except (KeyError,ValueError,InvalidOperation,TypeError):
        raise HTTPException(409,"Некорректная сумма платежа")
    if amount!=invoice.amount_minor or payment["amount"].get("currency")!=invoice.currency:
        raise HTTPException(409,"Сумма или валюта платежа не совпадает со счётом")
    if str(payment.get("metadata",{}).get("organization_id"))!=str(invoice.organization_id):
        raise HTTPException(409,"Организация платежа не совпадает со счётом")
    if invoice.provider_payment_id and invoice.provider_payment_id!=payment_id:
        raise HTTPException(409,"Счёт связан с другим платежом")
    invoice.provider_payment_id=payment_id
    apply_payment(db,invoice,reference="yookassa:"+payment_id,provider="yookassa")
    db.commit();return invoice_dict(invoice)
