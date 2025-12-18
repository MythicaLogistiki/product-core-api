-- Multi-tenant tables for Organization and Individual support
-- Supports path-based routing with tenant slugs

-- Tenant type enum
DO $$ BEGIN
    CREATE TYPE tenant_type AS ENUM ('organization', 'individual');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- User role enum
DO $$ BEGIN
    CREATE TYPE user_role AS ENUM (
        'owner', 'admin', 'member', 'viewer',
        'platform_admin', 'support_agent'
    );
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Tenants table
CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug VARCHAR(63) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    type tenant_type NOT NULL DEFAULT 'organization',
    owner_user_id VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT true,
    settings TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_tenants_slug ON tenants(slug);
CREATE INDEX IF NOT EXISTS ix_tenants_owner_user_id ON tenants(owner_user_id);
CREATE INDEX IF NOT EXISTS ix_tenants_type ON tenants(type);

-- User-Tenant mapping (many-to-many)
CREATE TABLE IF NOT EXISTS user_tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) NOT NULL,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    role user_role NOT NULL DEFAULT 'member',
    support_access_enabled BOOLEAN NOT NULL DEFAULT false,
    support_access_expires_at TIMESTAMPTZ,
    support_access_granted_by VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ,
    CONSTRAINT uq_user_tenant UNIQUE (user_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS ix_user_tenants_user_id ON user_tenants(user_id);
CREATE INDEX IF NOT EXISTS ix_user_tenants_tenant_id ON user_tenants(tenant_id);
CREATE INDEX IF NOT EXISTS ix_user_tenants_user_tenant ON user_tenants(user_id, tenant_id);

-- Support access audit log
CREATE TABLE IF NOT EXISTS support_access_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    support_user_id VARCHAR(255) NOT NULL,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    action VARCHAR(50) NOT NULL,
    reason TEXT,
    ip_address VARCHAR(45),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_support_access_logs_support_user ON support_access_logs(support_user_id);
CREATE INDEX IF NOT EXISTS ix_support_access_logs_tenant_time ON support_access_logs(tenant_id, created_at);

-- RLS Policies for tenants table
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenants_tenant_isolation ON tenants
    FOR ALL
    USING (
        id::text = current_setting('app.current_tenant', true)
        OR current_setting('app.is_platform_admin', true) = 'true'
        OR current_setting('app.support_access', true) = 'true'
    );

-- RLS Policies for user_tenants
ALTER TABLE user_tenants ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_tenants_isolation ON user_tenants
    FOR ALL
    USING (
        tenant_id::text = current_setting('app.current_tenant', true)
        OR current_setting('app.is_platform_admin', true) = 'true'
    );

-- Comments
COMMENT ON TABLE tenants IS 'Organizations and Individual tenants for multi-tenant SaaS';
COMMENT ON COLUMN tenants.slug IS 'URL-safe identifier for path-based routing (e.g., acme-tax)';
COMMENT ON COLUMN tenants.type IS 'organization = multi-user company, individual = solo user';
COMMENT ON TABLE user_tenants IS 'Many-to-many mapping of users to tenants with role assignments';
COMMENT ON COLUMN user_tenants.support_access_enabled IS 'Allows support agents temporary access';
COMMENT ON TABLE support_access_logs IS 'Audit trail for support agent access to customer tenants';
