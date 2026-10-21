"""Durable deliveries, tenant integration credentials, federation and billing ledger."""
from alembic import op
import sqlalchemy as sa

revision = "integrations_0023"
down_revision = "ai_0022"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('integration_operations',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('organization_id', sa.Integer, sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('external_id', sa.String(64), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('assignment_id', sa.Integer, sa.ForeignKey('learning_assignments.id'), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('organization_id', 'external_id', name='uq_integration_org_external_id'),
    )
    op.create_table('outbound_messages',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('organization_id', sa.Integer, sa.ForeignKey('organizations.id'), nullable=True),
        sa.Column('kind', sa.String(24), nullable=False),
        sa.Column('deduplication_key', sa.String(200), nullable=False, unique=True),
        sa.Column('payload_encrypted', sa.Text, nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('attempts', sa.Integer, nullable=False),
        sa.Column('available_at', sa.DateTime, nullable=False),
        sa.Column('lease_until', sa.DateTime, nullable=True),
        sa.Column('lease_token', sa.String(36), nullable=True),
        sa.Column('expires_at', sa.DateTime, nullable=True),
        sa.Column('delivered_at', sa.DateTime, nullable=True),
        sa.Column('last_error', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_outbound_messages_organization_id", "outbound_messages", ["organization_id"])
    op.create_index("ix_outbound_messages_available_at", "outbound_messages", ["available_at"])
    op.create_table('integration_keys',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('organization_id', sa.Integer, sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('name', sa.String(160), nullable=False),
        sa.Column('prefix', sa.String(24), nullable=False),
        sa.Column('key_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('scopes', sa.JSON, nullable=False),
        sa.Column('created_by', sa.Integer, sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime, nullable=False),
        sa.Column('revoked_at', sa.DateTime, nullable=True),
        sa.Column('last_used_at', sa.DateTime, nullable=True),
    )
    op.create_index("ix_integration_keys_organization_id", "integration_keys", ["organization_id"])
    op.create_table('webhook_endpoints',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('organization_id', sa.Integer, sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('url', sa.String(2048), nullable=False),
        sa.Column('secret_encrypted', sa.Text, nullable=False),
        sa.Column('events', sa.JSON, nullable=False),
        sa.Column('enabled', sa.Boolean, nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_webhook_endpoints_organization_id", "webhook_endpoints", ["organization_id"])
    op.create_table('oidc_providers',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('organization_id', sa.Integer, sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('name', sa.String(160), nullable=False),
        sa.Column('issuer', sa.String(2048), nullable=False),
        sa.Column('client_id', sa.String(255), nullable=False),
        sa.Column('client_secret_encrypted', sa.Text, nullable=False),
        sa.Column('authorization_endpoint', sa.String(2048), nullable=False),
        sa.Column('token_endpoint', sa.String(2048), nullable=False),
        sa.Column('jwks_uri', sa.String(2048), nullable=False),
        sa.Column('enabled', sa.Boolean, nullable=False),
        sa.Column('allow_provisioning', sa.Boolean, nullable=False),
        sa.Column('allowed_domains', sa.JSON, nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_oidc_providers_organization_id", "oidc_providers", ["organization_id"])
    op.create_table('external_identities',
        sa.UniqueConstraint('provider_id', 'subject', name='uq_identity_provider_subject'),
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('provider_id', sa.Integer, sa.ForeignKey('oidc_providers.id'), nullable=False),
        sa.Column('subject', sa.String(255), nullable=False),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_table('oidc_sessions',
        sa.Column('state_hash', sa.String(64), primary_key=True),
        sa.Column('provider_id', sa.Integer, sa.ForeignKey('oidc_providers.id'), nullable=False),
        sa.Column('nonce_hash', sa.String(64), nullable=False),
        sa.Column('browser_hash', sa.String(64), nullable=False),
        sa.Column('verifier_encrypted', sa.Text, nullable=False),
        sa.Column('link_user_id', sa.Integer, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('expires_at', sa.DateTime, nullable=False),
        sa.Column('consumed_at', sa.DateTime, nullable=True),
        sa.Column('exchange_code_hash', sa.String(64), nullable=True, unique=True),
        sa.Column('authenticated_user_id', sa.Integer, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('exchanged_at', sa.DateTime, nullable=True),
    )
    op.create_table('billing_accounts',
        sa.Column('organization_id', sa.Integer, sa.ForeignKey('organizations.id'), primary_key=True),
        sa.Column('plan_name', sa.String(160), nullable=False),
        sa.Column('contract_reference', sa.String(200), nullable=False),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('terms', sa.JSON, nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('access_until', sa.DateTime, nullable=True),
        sa.Column('retention_until', sa.DateTime, nullable=True),
        sa.Column('updated_at', sa.DateTime, nullable=False),
    )
    op.create_table('billing_invoices',
        sa.CheckConstraint('amount_minor > 0', name='ck_invoice_positive'),
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('organization_id', sa.Integer, sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('number', sa.String(100), nullable=False, unique=True),
        sa.Column('amount_minor', sa.BigInteger, nullable=False),
        sa.Column('currency', sa.String(3), nullable=False),
        sa.Column('description', sa.String(1000), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('due_at', sa.DateTime, nullable=False),
        sa.Column('service_until', sa.DateTime, nullable=False),
        sa.Column('paid_at', sa.DateTime, nullable=True),
        sa.Column('payment_reference', sa.String(200), nullable=True, unique=True),
        sa.Column('provider_payment_id', sa.String(100), nullable=True, unique=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_billing_invoices_organization_id", "billing_invoices", ["organization_id"])
    op.create_table('payment_events',
        sa.Column('id', sa.String(200), primary_key=True),
        sa.Column('invoice_id', sa.String(36), sa.ForeignKey('billing_invoices.id'), nullable=False),
        sa.Column('provider', sa.String(24), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('amount_minor', sa.BigInteger, nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_table('lti_platforms',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('organization_id', sa.Integer, sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('created_by', sa.Integer, sa.ForeignKey('users.id'), nullable=False),
        sa.Column('name', sa.String(160), nullable=False),
        sa.Column('issuer', sa.String(2048), nullable=False),
        sa.Column('client_id', sa.String(255), nullable=False),
        sa.Column('deployment_id', sa.String(255), nullable=False),
        sa.Column('authorization_endpoint', sa.String(2048), nullable=False),
        sa.Column('token_endpoint', sa.String(2048), nullable=False),
        sa.Column('jwks_uri', sa.String(2048), nullable=False),
        sa.Column('private_key_encrypted', sa.Text, nullable=False),
        sa.Column('public_jwk', sa.JSON, nullable=False),
        sa.Column('enabled', sa.Boolean, nullable=False),
        sa.Column('allow_provisioning', sa.Boolean, nullable=False),
    )
    op.create_index("ix_lti_platforms_organization_id", "lti_platforms", ["organization_id"])
    op.create_table('lti_resources',
        sa.UniqueConstraint('platform_id', 'resource_link_id', name='uq_lti_platform_resource'),
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('platform_id', sa.Integer, sa.ForeignKey('lti_platforms.id'), nullable=False),
        sa.Column('resource_link_id', sa.String(255), nullable=False),
        sa.Column('course_version_id', sa.Integer, sa.ForeignKey('course_versions.id'), nullable=False),
    )
    op.create_table('lti_identities',
        sa.UniqueConstraint('platform_id', 'subject', name='uq_lti_platform_subject'),
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('platform_id', sa.Integer, sa.ForeignKey('lti_platforms.id'), nullable=False),
        sa.Column('subject', sa.String(255), nullable=False),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), nullable=False),
    )
    op.create_table('lti_sessions',
        sa.Column('state_hash', sa.String(64), primary_key=True),
        sa.Column('platform_id', sa.Integer, sa.ForeignKey('lti_platforms.id'), nullable=False),
        sa.Column('nonce_hash', sa.String(64), nullable=False),
        sa.Column('browser_hash', sa.String(64), nullable=False),
        sa.Column('expires_at', sa.DateTime, nullable=False),
        sa.Column('consumed_at', sa.DateTime, nullable=True),
        sa.Column('exchange_code_hash', sa.String(64), nullable=True, unique=True),
        sa.Column('authenticated_user_id', sa.Integer, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('assignment_id', sa.Integer, sa.ForeignKey('learning_assignments.id'), nullable=True),
        sa.Column('exchanged_at', sa.DateTime, nullable=True),
    )
    op.create_table('lti_grade_bindings',
        sa.UniqueConstraint('platform_id', 'assignment_id', 'subject', name='uq_lti_assignment_grade_binding'),
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('platform_id', sa.Integer, sa.ForeignKey('lti_platforms.id'), nullable=False),
        sa.Column('assignment_id', sa.Integer, sa.ForeignKey('learning_assignments.id'), nullable=False),
        sa.Column('subject', sa.String(255), nullable=False),
        sa.Column('lineitem_url', sa.String(2048), nullable=False),
        sa.Column('last_queued_at', sa.DateTime, nullable=True),
        sa.Column('last_delivered_at', sa.DateTime, nullable=True),
    )
    op.create_index("ix_lti_grade_bindings_assignment_id", "lti_grade_bindings", ["assignment_id"])


def downgrade():
    op.drop_table('integration_operations')
    op.drop_table('lti_grade_bindings')
    op.drop_table('lti_sessions')
    op.drop_table('lti_identities')
    op.drop_table('lti_resources')
    op.drop_table('lti_platforms')
    op.drop_table('payment_events')
    op.drop_table('billing_invoices')
    op.drop_table('billing_accounts')
    op.drop_table('oidc_sessions')
    op.drop_table('external_identities')
    op.drop_table('oidc_providers')
    op.drop_table('webhook_endpoints')
    op.drop_table('integration_keys')
    op.drop_table('outbound_messages')
