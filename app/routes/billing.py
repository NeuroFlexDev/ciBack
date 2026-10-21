import uuid
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.models.integrations import BillingAccount, Invoice
from app.services.auth_service import require_admin
from app.services.integration_access import billing_owner
from app.services.organization_access import audit, lock_org
from app.services.billing_service import apply_payment, invoice_dict, reconcile_payment

router=APIRouter()


class BillingTerms(BaseModel):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)
    plan_name:str=Field(min_length=2,max_length=160)
    contract_reference:str=Field(min_length=2,max_length=200)
    currency:Literal["RUB","USD","EUR"]="RUB"
    status:Literal["pending","active","suspended","cancelled"]="pending"
    access_until:datetime|None=None
    retention_until:datetime|None=None
    ai_charging:str=Field(min_length=5,max_length=2000)
    renewal_terms:str=Field(min_length=5,max_length=2000)
    cancellation_terms:str=Field(min_length=5,max_length=2000)
    refund_terms:str=Field(min_length=5,max_length=2000)
    data_retention_terms:str=Field(min_length=5,max_length=2000)
    reason:str=Field(min_length=5,max_length=1000)
    @field_validator("access_until","retention_until")
    @classmethod
    def utc(cls,value):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value and value.tzinfo else value
    @model_validator(mode="after")
    def retention(self):
        if self.status=="active" and not self.access_until:raise ValueError("Active contract requires access_until")
        if self.access_until and self.retention_until and self.retention_until<self.access_until:raise ValueError("Retention must not end before access")
        return self


def account_dict(account):
    if not account:return None
    result={key:getattr(account,key) for key in ("plan_name","contract_reference","currency","terms","status","access_until","retention_until","updated_at")}
    result["effective_status"]="expired" if account.status=="active" and account.access_until and account.access_until<=datetime.utcnow() else account.status
    result["data_deleted"]=False
    return result


@router.get("/billing")
def billing(offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),db:Session=Depends(get_db),user=Depends(billing_owner)):
    return billing_view(db,user.organization_id,offset,limit)


def billing_view(db,organization_id,offset=0,limit=50):
    account=db.get(BillingAccount,organization_id)
    query=db.query(Invoice).filter_by(organization_id=organization_id)
    org=lock_org(db,organization_id)
    return {"account":account_dict(account),"invoices":{"total":query.count(),"items":[invoice_dict(row) for row in query.order_by(Invoice.created_at.desc()).offset(offset).limit(limit)]},
        "quotas":{key:getattr(org,key) for key in ("seat_limit","course_limit","source_limit","generation_limit")}}


@router.get("/admin/organizations/{org_id}/billing")
def admin_billing(org_id:int,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=100),db:Session=Depends(get_db),admin=Depends(require_admin)):
    return billing_view(db,org_id,offset,limit)


@router.put("/admin/organizations/{org_id}/billing")
def set_billing(org_id:int,payload:BillingTerms,db:Session=Depends(get_db),admin=Depends(require_admin)):
    lock_org(db,org_id)
    row=db.get(BillingAccount,org_id)
    if not row:row=BillingAccount(organization_id=org_id);db.add(row)
    for key in ("plan_name","contract_reference","currency","status","access_until","retention_until"):setattr(row,key,getattr(payload,key))
    row.terms=payload.model_dump(include={"ai_charging","renewal_terms","cancellation_terms","refund_terms","data_retention_terms"})
    audit(db,admin,"billing.changed","organization",org_id,reason=payload.reason,status=payload.status)
    db.commit();return account_dict(row)


class InvoiceInput(BaseModel):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)
    number:str=Field(min_length=1,max_length=100)
    amount_minor:int=Field(gt=0,le=100000000000)
    description:str=Field(min_length=5,max_length=1000)
    due_at:datetime
    service_until:datetime
    @field_validator("due_at","service_until")
    @classmethod
    def utc(cls,value):return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


@router.post("/admin/organizations/{org_id}/invoices",status_code=201)
def create_invoice(org_id:int,payload:InvoiceInput,db:Session=Depends(get_db),admin=Depends(require_admin)):
    lock_org(db,org_id)
    account=db.get(BillingAccount,org_id)
    if not account:raise HTTPException(409,"Сначала согласуйте и сохраните условия договора")
    existing=db.query(Invoice).filter_by(number=payload.number).first()
    if existing:
        if existing.organization_id==org_id and all(getattr(existing,k)==v for k,v in payload.model_dump().items()):return invoice_dict(existing)
        raise HTTPException(409,"Номер счёта уже используется с другими реквизитами")
    if payload.service_until<=datetime.utcnow():raise HTTPException(422,"Дата окончания услуги должна быть в будущем")
    row=Invoice(id=str(uuid.uuid4()),organization_id=org_id,currency=account.currency,**payload.model_dump())
    db.add(row);db.flush();audit(db,admin,"invoice.created","invoice",row.id,organization_id=org_id)
    db.commit();return invoice_dict(row)


class PaymentConfirmation(BaseModel):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)
    payment_reference:str=Field(min_length=3,max_length=180)
    reason:str=Field(min_length=10,max_length=1000)


@router.post("/admin/invoices/{invoice_id}/confirm")
def confirm_invoice(invoice_id:str,payload:PaymentConfirmation,db:Session=Depends(get_db),admin=Depends(require_admin)):
    row=db.query(Invoice).filter_by(id=invoice_id).with_for_update().first()
    if not row:raise HTTPException(404,"Счёт не найден")
    apply_payment(db,row,reference="bank:"+payload.payment_reference,provider="bank",actor=admin,reason=payload.reason)
    db.commit();return invoice_dict(row)


class YooKassaNotification(BaseModel):
    type:Literal["notification"]
    event:str=Field(max_length=100)
    object:dict


@router.post("/billing/webhooks/yookassa")
def payment_notification(payload:YooKassaNotification,db:Session=Depends(get_db)):
    payment_id=payload.object.get("id","")
    try:uuid.UUID(payment_id)
    except (ValueError,TypeError,AttributeError):raise HTTPException(422,"Некорректный идентификатор платежа")
    # The notification body is only a wake-up signal. State, metadata and amount come from authenticated provider API.
    if payload.event!="payment.succeeded":return {"received":True,"applied":False}
    reconcile_payment(db,payment_id)
    return {"received":True,"applied":True}


@router.post("/admin/payments/{payment_id}/reconcile")
def reconcile(payment_id:uuid.UUID,db:Session=Depends(get_db),admin=Depends(require_admin)):
    return reconcile_payment(db,str(payment_id))
