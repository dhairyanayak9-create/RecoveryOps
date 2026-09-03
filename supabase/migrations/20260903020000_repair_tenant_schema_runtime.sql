DO $$
DECLARE
  s text;
  t text;
  seq text;
  max_id bigint;
  tables text[] := ARRAY['bank','vehicle','status_history_legacy','notification','sync_mutation','client','recovery_case','app_file','challan','chat_conversation','chat_message','report'];
BEGIN
  FOR s IN SELECT schema_name FROM public.agency_tenants LOOP
    -- User identity and OTP stay centralized in public.
    EXECUTE format('DROP TABLE IF EXISTS %I.user_otp CASCADE', s);
    EXECUTE format('DROP TABLE IF EXISTS %I."user" CASCADE', s);

    FOREACH t IN ARRAY tables LOOP
      IF to_regclass(format('%I.%I', s, t)) IS NOT NULL AND t <> 'sync_mutation' THEN
        seq := format('%I.%I_id_seq', s, t);
        EXECUTE format('CREATE SEQUENCE IF NOT EXISTS %s', seq);
        EXECUTE format('ALTER SEQUENCE %s OWNED BY %I.%I.id', seq, s, t);
        EXECUTE format('ALTER TABLE %I.%I ALTER COLUMN id SET DEFAULT nextval(%L::regclass)', s, t, seq);
        EXECUTE format('SELECT max(id) FROM %I.%I', s, t) INTO max_id;
        IF max_id IS NULL THEN
          EXECUTE format('SELECT setval(%L::regclass, 1, false)', seq);
        ELSE
          EXECUTE format('SELECT setval(%L::regclass, %s, true)', seq, max_id);
        END IF;
      END IF;
    END LOOP;

    IF to_regclass(format('%I.data_assignment', s)) IS NOT NULL THEN
      seq := format('%I.data_assignment_id_seq', s);
      EXECUTE format('CREATE SEQUENCE IF NOT EXISTS %s', seq);
      EXECUTE format('ALTER SEQUENCE %s OWNED BY %I.data_assignment.id', seq, s);
      EXECUTE format('ALTER TABLE %I.data_assignment ALTER COLUMN id SET DEFAULT nextval(%L::regclass)', s, seq);
      EXECUTE format('SELECT max(id) FROM %I.data_assignment', s) INTO max_id;
      IF max_id IS NULL THEN
        EXECUTE format('SELECT setval(%L::regclass, 1, false)', seq);
      ELSE
        EXECUTE format('SELECT setval(%L::regclass, %s, true)', seq, max_id);
      END IF;
    END IF;

    -- Backfill legacy public rows into their isolated store without overwriting tenant rows.
    FOREACH t IN ARRAY tables LOOP
      IF to_regclass(format('public.%I', t)) IS NOT NULL AND to_regclass(format('%I.%I', s, t)) IS NOT NULL THEN
        EXECUTE format('INSERT INTO %I.%I SELECT * FROM public.%I WHERE agency_id=(SELECT legacy_agency_id FROM public.agency_tenants WHERE schema_name=%L) ON CONFLICT DO NOTHING', s, t, t, s);
      END IF;
    END LOOP;
  END LOOP;
END $$;
