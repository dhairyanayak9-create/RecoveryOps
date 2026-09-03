DO $$
DECLARE s text;
BEGIN
  FOR s IN SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'recovery_agency_%' LOOP
    EXECUTE format('REVOKE ALL ON SCHEMA %I FROM anon, authenticated', s);
    EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM anon, authenticated', s);
    EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM anon, authenticated', s);
    EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA %I FROM anon, authenticated', s);
  END LOOP;
END $$;

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
    EXECUTE format('REVOKE ALL ON SCHEMA %I FROM anon, authenticated', schema_name);
    EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM anon, authenticated', schema_name);
    EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM anon, authenticated', schema_name);
    EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA %I FROM anon, authenticated', schema_name);
    INSERT INTO public.agency_tenants(agency_id, legacy_agency_id, schema_name)
    VALUES (NEW.id, NULL, schema_name)
    ON CONFLICT (agency_id) DO UPDATE SET schema_name=EXCLUDED.schema_name;
    RETURN NEW;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.provision_agency_schema() FROM PUBLIC, anon, authenticated;
