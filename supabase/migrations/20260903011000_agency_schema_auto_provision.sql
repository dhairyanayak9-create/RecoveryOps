ALTER TABLE public.agency_tenants ALTER COLUMN legacy_agency_id DROP NOT NULL;

CREATE OR REPLACE FUNCTION public.provision_agency_schema()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    schema_name text := 'recovery_agency_' || NEW.id::text;
    t text;
BEGIN
    EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', schema_name);
    FOREACH t IN ARRAY ARRAY[
        'user_otp','bank','vehicle','status_history_legacy','notification',
        'sync_mutation','client','recovery_case','app_file','challan',
        'chat_conversation','chat_message','report'
    ] LOOP
        IF to_regclass('public.' || t) IS NOT NULL THEN
            EXECUTE format('CREATE TABLE IF NOT EXISTS %I.%I (LIKE public.%I INCLUDING ALL)', schema_name, t, t);
        END IF;
    END LOOP;
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
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I.data_assignment (agency_id)', 'ix_data_assignment_agency_id', schema_name);
    INSERT INTO public.agency_tenants(agency_id, legacy_agency_id, schema_name)
    VALUES (NEW.id, NULL, schema_name)
    ON CONFLICT (agency_id) DO UPDATE SET schema_name=EXCLUDED.schema_name;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.link_agency_company_schema()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    modern_id bigint;
BEGIN
    SELECT id INTO modern_id
    FROM public.agencies
    WHERE lower(name)=lower(NEW.name)
       OR upper(slug)=upper(COALESCE(NEW.code,''))
    ORDER BY id DESC
    LIMIT 1;

    IF modern_id IS NOT NULL THEN
        UPDATE public.agency_tenants at
        SET legacy_agency_id = NEW.id,
            schema_name = 'recovery_agency_' || at.agency_id::text
        WHERE at.agency_id = modern_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_provision_agency_schema ON public.agencies;
CREATE TRIGGER trg_provision_agency_schema
AFTER INSERT ON public.agencies
FOR EACH ROW EXECUTE FUNCTION public.provision_agency_schema();

DROP TRIGGER IF EXISTS trg_link_agency_company_schema ON public.agency_company;
CREATE TRIGGER trg_link_agency_company_schema
AFTER INSERT ON public.agency_company
FOR EACH ROW EXECUTE FUNCTION public.link_agency_company_schema();

DO $$
DECLARE
    a RECORD;
    s text;
    t text;
BEGIN
    FOR a IN SELECT agency_id, schema_name FROM public.agency_tenants LOOP
        s := a.schema_name;
        FOREACH t IN ARRAY ARRAY['user_otp','bank','vehicle','status_history_legacy','notification','sync_mutation','client','recovery_case','app_file','challan','chat_conversation','chat_message','report'] LOOP
            IF to_regclass('public.' || t) IS NOT NULL THEN
                EXECUTE format('CREATE TABLE IF NOT EXISTS %I.%I (LIKE public.%I INCLUDING ALL)', s, t, t);
            END IF;
        END LOOP;
    END LOOP;
END $$;

REVOKE EXECUTE ON FUNCTION public.provision_agency_schema() FROM PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.link_agency_company_schema() FROM PUBLIC, anon, authenticated;
