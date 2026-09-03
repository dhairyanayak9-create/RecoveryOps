-- RecoveryOps: per-agency PostgreSQL schema isolation.
-- Control-plane tables stay in public. Tenant business tables are copied into
-- recovery_agency_<legacy_agency_id> and application requests select that schema.

CREATE TABLE IF NOT EXISTS public.agency_tenants (
    agency_id BIGINT PRIMARY KEY REFERENCES public.agencies(id) ON DELETE RESTRICT,
    legacy_agency_id INTEGER NOT NULL UNIQUE REFERENCES public.agency_company(id) ON DELETE RESTRICT,
    schema_name TEXT NOT NULL UNIQUE,
    provisioned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_version INTEGER NOT NULL DEFAULT 1
);

DO $$
DECLARE
    a RECORD;
    schema_name TEXT;
    legacy_id INTEGER;
    table_name TEXT;
BEGIN
    FOR a IN
        SELECT ag.id AS agency_id, ag.name, ag.slug,
               COALESCE(ac.id, ag.id::INTEGER) AS legacy_id
        FROM public.agencies ag
        LEFT JOIN public.agency_company ac
          ON ac.supabase_agency_id::text = ag.id::text
          OR lower(ac.name) = lower(ag.name)
          OR upper(ac.code) = upper(ag.slug)
        WHERE ag.is_active = true
        ORDER BY ag.id
    LOOP
        legacy_id := a.legacy_id;
        schema_name := 'recovery_agency_' || legacy_id::text;

        EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', schema_name);

        FOREACH table_name IN ARRAY ARRAY[
            'bank', 'vehicle', 'status_history_legacy', 'notification',
            'sync_mutation', 'client', 'recovery_case', 'app_file', 'challan',
            'chat_conversation', 'chat_message', 'report'
        ] LOOP
            IF to_regclass('public.' || table_name) IS NOT NULL THEN
                EXECUTE format(
                    'CREATE TABLE IF NOT EXISTS %I.%I (LIKE public.%I INCLUDING ALL)',
                    schema_name, table_name, table_name
                );
                EXECUTE format(
                    'INSERT INTO %I.%I SELECT * FROM public.%I WHERE agency_id = $1 ON CONFLICT DO NOTHING',
                    schema_name, table_name, table_name
                ) USING legacy_id;
            END IF;
        END LOOP;

        -- The ORM's legacy DataAssignment model uses this shape. The public
        -- data_assignment table is a different UUID-era compatibility table.
        EXECUTE format($sql$
            CREATE TABLE IF NOT EXISTS %I.data_assignment (
                id INTEGER PRIMARY KEY,
                agency_id INTEGER,
                user_id INTEGER NOT NULL,
                bank_id INTEGER,
                vehicle_id INTEGER,
                permission VARCHAR(30) DEFAULT 'READ',
                active BOOLEAN DEFAULT true,
                created_at TIMESTAMP DEFAULT now()
            )
        $sql$, schema_name);

        EXECUTE format($sql$
            CREATE INDEX IF NOT EXISTS %I ON %I.data_assignment (agency_id)
        $sql$, 'ix_data_assignment_agency_id', schema_name);

        INSERT INTO public.agency_tenants(agency_id, legacy_agency_id, schema_name)
        VALUES (a.agency_id, legacy_id, schema_name)
        ON CONFLICT (agency_id) DO UPDATE
        SET legacy_agency_id = EXCLUDED.legacy_agency_id,
            schema_name = EXCLUDED.schema_name,
            schema_version = GREATEST(public.agency_tenants.schema_version, 1);
    END LOOP;
END $$;

-- Public legacy business tables are retained as a compatibility archive, but
-- must not be readable/writable through Supabase's anon/authenticated API roles.
REVOKE ALL ON TABLE
    public.challan,
    public.agency_company,
    public.user_otp,
    public.bank,
    public.notification,
    public.client,
    public.chat_conversation,
    public.report,
    public.app_file,
    public.chat_message,
    public.vehicle,
    public.status_history_legacy,
    public.sync_mutation,
    public.recovery_case,
    public.agency_subscriptions,
    public.data_assignment
FROM anon, authenticated;

-- Custom tenant schemas are intentionally not exposed to PostgREST.
-- The Flask backend connects with the database role and selects the tenant
-- schema per authenticated request.
