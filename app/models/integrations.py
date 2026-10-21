"""Tenant-scoped integrations, durable deliveries and billing ledger."""
from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from app.database.db import Base


class OutboundMessage(Base):
    __tablename__ = "outbound_messages"
    id = Column(String(36), primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    kind = Column(String(24), nullable=False)
    deduplication_key = Column(String(200), nullable=False, unique=True)
    payload_encrypted = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime, nullable=False, default=func.now(), index=True)
    lease_until = Column(DateTime, nullable=True)
    lease_token = Column(String(36), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)
    last_error = Column(String(100), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class IntegrationKey(Base):
    __tablename__ = "integration_keys"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String(160), nullable=False)
    prefix = Column(String(24), nullable=False)
    key_hash = Column(String(64), nullable=False, unique=True)
    scopes = Column(JSON, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)


class IntegrationOperation(Base):
    __tablename__ = "integration_operations"
    __table_args__ = (UniqueConstraint("organization_id", "external_id", name="uq_integration_org_external_id"),)
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    external_id = Column(String(64), nullable=False)
    request_hash = Column(String(64), nullable=False)
    assignment_id = Column(Integer, ForeignKey("learning_assignments.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    url = Column(String(2048), nullable=False)
    secret_encrypted = Column(Text, nullable=False)
    events = Column(JSON, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class OIDCProvider(Base):
    __tablename__ = "oidc_providers"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String(160), nullable=False)
    issuer = Column(String(2048), nullable=False)
    client_id = Column(String(255), nullable=False)
    client_secret_encrypted = Column(Text, nullable=False)
    authorization_endpoint = Column(String(2048), nullable=False)
    token_endpoint = Column(String(2048), nullable=False)
    jwks_uri = Column(String(2048), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    allow_provisioning = Column(Boolean, nullable=False, default=False)
    allowed_domains = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class ExternalIdentity(Base):
    __tablename__ = "external_identities"
    __table_args__ = (UniqueConstraint("provider_id", "subject", name="uq_identity_provider_subject"),)
    id = Column(Integer, primary_key=True)
    provider_id = Column(Integer, ForeignKey("oidc_providers.id"), nullable=False)
    subject = Column(String(255), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class OIDCSession(Base):
    __tablename__ = "oidc_sessions"
    state_hash = Column(String(64), primary_key=True)
    provider_id = Column(Integer, ForeignKey("oidc_providers.id"), nullable=False)
    nonce_hash = Column(String(64), nullable=False)
    browser_hash = Column(String(64), nullable=False)
    verifier_encrypted = Column(Text, nullable=False)
    link_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)
    exchange_code_hash = Column(String(64), nullable=True, unique=True)
    authenticated_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    exchanged_at = Column(DateTime, nullable=True)


class BillingAccount(Base):
    __tablename__ = "billing_accounts"
    organization_id = Column(Integer, ForeignKey("organizations.id"), primary_key=True)
    plan_name = Column(String(160), nullable=False)
    contract_reference = Column(String(200), nullable=False)
    currency = Column(String(3), nullable=False, default="RUB")
    terms = Column(JSON, nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    access_until = Column(DateTime, nullable=True)
    retention_until = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())


class Invoice(Base):
    __tablename__ = "billing_invoices"
    __table_args__ = (CheckConstraint("amount_minor > 0", name="ck_invoice_positive"),)
    id = Column(String(36), primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    number = Column(String(100), nullable=False, unique=True)
    amount_minor = Column(BigInteger, nullable=False)
    currency = Column(String(3), nullable=False)
    description = Column(String(1000), nullable=False)
    status = Column(String(20), nullable=False, default="issued")
    due_at = Column(DateTime, nullable=False)
    service_until = Column(DateTime, nullable=False)
    paid_at = Column(DateTime, nullable=True)
    payment_reference = Column(String(200), nullable=True, unique=True)
    provider_payment_id = Column(String(100), nullable=True, unique=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class PaymentEvent(Base):
    __tablename__ = "payment_events"
    id = Column(String(200), primary_key=True)
    invoice_id = Column(String(36), ForeignKey("billing_invoices.id"), nullable=False)
    provider = Column(String(24), nullable=False)
    status = Column(String(24), nullable=False)
    amount_minor = Column(BigInteger, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.now())


class LTIPlatform(Base):
    __tablename__ = "lti_platforms"
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(160), nullable=False)
    issuer = Column(String(2048), nullable=False)
    client_id = Column(String(255), nullable=False)
    deployment_id = Column(String(255), nullable=False)
    authorization_endpoint = Column(String(2048), nullable=False)
    token_endpoint = Column(String(2048), nullable=False)
    jwks_uri = Column(String(2048), nullable=False)
    private_key_encrypted = Column(Text, nullable=False)
    public_jwk = Column(JSON, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    allow_provisioning = Column(Boolean, nullable=False, default=False)


class LTIResource(Base):
    __tablename__ = "lti_resources"
    __table_args__ = (UniqueConstraint("platform_id", "resource_link_id", name="uq_lti_platform_resource"),)
    id = Column(Integer, primary_key=True)
    platform_id = Column(Integer, ForeignKey("lti_platforms.id"), nullable=False)
    resource_link_id = Column(String(255), nullable=False)
    course_version_id = Column(Integer, ForeignKey("course_versions.id"), nullable=False)


class LTIIdentity(Base):
    __tablename__ = "lti_identities"
    __table_args__ = (UniqueConstraint("platform_id", "subject", name="uq_lti_platform_subject"),)
    id = Column(Integer, primary_key=True)
    platform_id = Column(Integer, ForeignKey("lti_platforms.id"), nullable=False)
    subject = Column(String(255), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)


class LTISession(Base):
    __tablename__ = "lti_sessions"
    state_hash = Column(String(64), primary_key=True)
    platform_id = Column(Integer, ForeignKey("lti_platforms.id"), nullable=False)
    nonce_hash = Column(String(64), nullable=False)
    browser_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime, nullable=True)
    exchange_code_hash = Column(String(64), nullable=True, unique=True)
    authenticated_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    assignment_id = Column(Integer, ForeignKey("learning_assignments.id"), nullable=True)
    exchanged_at = Column(DateTime, nullable=True)


class LTIGradeBinding(Base):
    __tablename__ = "lti_grade_bindings"
    __table_args__ = (UniqueConstraint("platform_id", "assignment_id", "subject", name="uq_lti_assignment_grade_binding"),)
    id = Column(Integer, primary_key=True)
    platform_id = Column(Integer, ForeignKey("lti_platforms.id"), nullable=False)
    assignment_id = Column(Integer, ForeignKey("learning_assignments.id"), nullable=False, index=True)
    subject = Column(String(255), nullable=False)
    lineitem_url = Column(String(2048), nullable=False)
    last_queued_at = Column(DateTime, nullable=True)
    last_delivered_at = Column(DateTime, nullable=True)
