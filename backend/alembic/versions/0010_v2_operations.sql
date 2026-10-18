-- 运维身份与业务身份分开；管理员仍受所有现有私有数据RLS约束。
CREATE TABLE admin_memberships (
 user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE admin_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_memberships FORCE ROW LEVEL SECURITY;
CREATE POLICY admin_guard ON admin_memberships TO itemtalk_guard USING(true);
CREATE POLICY admin_provision ON admin_memberships TO itemtalk_migrator USING(true) WITH CHECK(true);
REVOKE ALL ON admin_memberships FROM PUBLIC,itemtalk_app,itemtalk_worker;
GRANT SELECT ON admin_memberships TO itemtalk_guard;
GRANT SELECT,INSERT,DELETE ON admin_memberships TO itemtalk_migrator;

CREATE FUNCTION itemtalk_is_admin() RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 SELECT EXISTS(SELECT 1 FROM public.admin_memberships a JOIN public.users u ON u.id=a.user_id
 WHERE a.user_id=public.itemtalk_actor() AND u.status='active' AND u.deleted_at IS NULL)
$$;
ALTER FUNCTION itemtalk_is_admin() OWNER TO itemtalk_guard;
REVOKE ALL ON FUNCTION itemtalk_is_admin() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION itemtalk_is_admin() TO itemtalk_app,itemtalk_worker,itemtalk_migrator;

CREATE TABLE runtime_config (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 version integer NOT NULL DEFAULT 1 CHECK(version>0),
 global_limit integer CHECK(global_limit BETWEEN 1 AND 1000000),
 subject_limit integer CHECK(subject_limit BETWEEN 1 AND 1000000),
 background_paused boolean NOT NULL DEFAULT false,
 chat_profile varchar(64) NOT NULL DEFAULT 'current',
 updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO runtime_config(singleton) VALUES(true);
CREATE TABLE runtime_config_audit (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 actor_id uuid,
 version integer NOT NULL,
 before_value jsonb NOT NULL,
 after_value jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE telemetry_events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 kind varchar(20) NOT NULL CHECK(kind IN ('http','upstream','retrieval','queue','cache')),
 operation varchar(160) NOT NULL,
 provider varchar(40) NOT NULL DEFAULT '', model varchar(100) NOT NULL DEFAULT '',
 outcome varchar(40) NOT NULL, duration_ms double precision NOT NULL CHECK(duration_ms>=0),
 first_token_ms double precision CHECK(first_token_ms>=0),
 input_tokens bigint CHECK(input_tokens>=0), output_tokens bigint CHECK(output_tokens>=0),
 estimated_cost numeric(24,10) CHECK(estimated_cost>=0), currency varchar(8) NOT NULL DEFAULT '',
 price_version varchar(40) NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_telemetry_time ON telemetry_events(created_at);
CREATE TABLE telemetry_daily (
 day date NOT NULL, kind varchar(20) NOT NULL, operation varchar(160) NOT NULL,
 provider varchar(40) NOT NULL, model varchar(100) NOT NULL,outcome varchar(40) NOT NULL,
 currency varchar(8) NOT NULL, duration_bucket integer NOT NULL,
 count bigint NOT NULL, duration_total double precision NOT NULL,
 input_known bigint NOT NULL, input_total bigint NOT NULL,
 output_known bigint NOT NULL, output_total bigint NOT NULL,
 cost_known bigint NOT NULL, cost_total numeric(24,10) NOT NULL,
 PRIMARY KEY(day,kind,operation,provider,model,outcome,currency,duration_bucket)
);
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['runtime_config','runtime_config_audit','telemetry_events','telemetry_daily'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY ops_guard ON %I TO itemtalk_guard USING(true) WITH CHECK(true)',t);
  EXECUTE format('REVOKE ALL ON %I FROM PUBLIC,itemtalk_app,itemtalk_worker',t);
  EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON %I TO itemtalk_guard',t);
  EXECUTE format('CREATE POLICY admin_read ON %I FOR SELECT TO itemtalk_app USING(public.itemtalk_is_admin())',t);
  EXECUTE format('GRANT SELECT ON %I TO itemtalk_app',t);
 END LOOP;
END $$;
CREATE POLICY runtime_read ON runtime_config FOR SELECT TO itemtalk_app,itemtalk_worker USING(true);
GRANT SELECT ON runtime_config TO itemtalk_worker;
GRANT USAGE,SELECT ON SEQUENCE telemetry_events_id_seq,runtime_config_audit_id_seq TO itemtalk_guard;

CREATE FUNCTION itemtalk_update_runtime(expected integer,g integer,s integer,paused boolean,profile text)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE old public.runtime_config; changed public.runtime_config;
BEGIN
 IF NOT public.itemtalk_is_admin() THEN RAISE EXCEPTION 'Administrator required'; END IF;
 SELECT * INTO old FROM public.runtime_config WHERE singleton FOR UPDATE;
 IF old.version<>expected THEN RETURN NULL; END IF;
 IF length(profile)>64 OR profile !~ '^[a-zA-Z0-9_-]+$' THEN RAISE EXCEPTION 'Invalid profile'; END IF;
 UPDATE public.runtime_config SET version=version+1,global_limit=g,subject_limit=s,
 background_paused=paused,chat_profile=profile,updated_at=now() WHERE singleton RETURNING * INTO changed;
 INSERT INTO public.runtime_config_audit(actor_id,version,before_value,after_value)
 VALUES(public.itemtalk_actor(),changed.version,to_jsonb(old),to_jsonb(changed));
 RETURN to_jsonb(changed);
END $$;
CREATE FUNCTION itemtalk_record_metric(k text,op text,p text,m text,o text,ms double precision,
 first_ms double precision,it bigint,ot bigint,cost numeric,cur text,pv text)
 RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 INSERT INTO public.telemetry_events(kind,operation,provider,model,outcome,duration_ms,
 first_token_ms,input_tokens,output_tokens,estimated_cost,currency,price_version)
 VALUES(k,op,p,m,o,ms,first_ms,it,ot,cost,cur,pv)
$$;
CREATE FUNCTION itemtalk_admin_queue_stats() RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
DECLARE result jsonb; BEGIN
 IF NOT public.itemtalk_is_admin() THEN RAISE EXCEPTION 'Administrator required'; END IF;
 SELECT COALESCE(jsonb_agg(to_jsonb(q)),'[]'::jsonb) INTO result FROM
 (SELECT job_type,status,count(*) AS count,
 max(CASE WHEN status='queued' THEN GREATEST(0,extract(epoch FROM now()-available_at)) ELSE 0 END) AS oldest_wait_seconds
 FROM public.jobs GROUP BY job_type,status) q;
 RETURN result;
END $$;
CREATE FUNCTION itemtalk_ops_cleanup() RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
DECLARE n integer; BEGIN
 -- 一次只处理500条明细；删除和累加汇总同事务，崩溃不会丢失或重复累计。
 WITH selected AS (SELECT id FROM public.telemetry_events WHERE created_at<now()-interval '30 days'
 ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 500), removed AS (
 DELETE FROM public.telemetry_events WHERE id IN(SELECT id FROM selected) RETURNING *), grouped AS (
 SELECT (created_at AT TIME ZONE 'UTC')::date AS day,kind,operation,provider,model,outcome,currency,
 width_bucket(duration_ms,ARRAY[10,25,50,100,200,500,1000,2500,5000,10000,30000,60000,180000]::double precision[]) AS duration_bucket,
 count(*) AS count,sum(duration_ms) AS duration_total,count(input_tokens) AS input_known,
 COALESCE(sum(input_tokens),0) AS input_total,count(output_tokens) AS output_known,
 COALESCE(sum(output_tokens),0) AS output_total,count(estimated_cost) AS cost_known,
 COALESCE(sum(estimated_cost),0) AS cost_total FROM removed
 GROUP BY 1,2,3,4,5,6,7,8)
 INSERT INTO public.telemetry_daily SELECT * FROM grouped
 ON CONFLICT(day,kind,operation,provider,model,outcome,currency,duration_bucket) DO UPDATE SET
 count=telemetry_daily.count+EXCLUDED.count,duration_total=telemetry_daily.duration_total+EXCLUDED.duration_total,
 input_known=telemetry_daily.input_known+EXCLUDED.input_known,input_total=telemetry_daily.input_total+EXCLUDED.input_total,
 output_known=telemetry_daily.output_known+EXCLUDED.output_known,output_total=telemetry_daily.output_total+EXCLUDED.output_total,
 cost_known=telemetry_daily.cost_known+EXCLUDED.cost_known,cost_total=telemetry_daily.cost_total+EXCLUDED.cost_total;
 GET DIAGNOSTICS n=ROW_COUNT;
 DELETE FROM public.telemetry_daily WHERE ctid IN(SELECT ctid FROM public.telemetry_daily
 WHERE day<(now() AT TIME ZONE 'UTC')::date-365 LIMIT 500);
 DELETE FROM public.query_embedding_cache WHERE ctid IN(SELECT ctid FROM public.query_embedding_cache
 WHERE expires_at<now() LIMIT 500);
 RETURN n;
END $$;
ALTER FUNCTION itemtalk_update_runtime(integer,integer,integer,boolean,text) OWNER TO itemtalk_guard;
ALTER FUNCTION itemtalk_record_metric(text,text,text,text,text,double precision,double precision,bigint,bigint,numeric,text,text) OWNER TO itemtalk_guard;
ALTER FUNCTION itemtalk_admin_queue_stats() OWNER TO itemtalk_guard;
ALTER FUNCTION itemtalk_ops_cleanup() OWNER TO itemtalk_guard;
REVOKE ALL ON FUNCTION itemtalk_update_runtime(integer,integer,integer,boolean,text),
 itemtalk_record_metric(text,text,text,text,text,double precision,double precision,bigint,bigint,numeric,text,text),
 itemtalk_admin_queue_stats(),itemtalk_ops_cleanup() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION itemtalk_update_runtime(integer,integer,integer,boolean,text),itemtalk_admin_queue_stats() TO itemtalk_app;
GRANT EXECUTE ON FUNCTION itemtalk_record_metric(text,text,text,text,text,double precision,double precision,bigint,bigint,numeric,text,text) TO itemtalk_app,itemtalk_worker;
GRANT EXECUTE ON FUNCTION itemtalk_ops_cleanup() TO itemtalk_worker;

CREATE TABLE query_embedding_cache (
 user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 cache_key varchar(64) NOT NULL, embedding vector(1024) NOT NULL,
 expires_at timestamptz NOT NULL,PRIMARY KEY(user_id,cache_key)
);
ALTER TABLE query_embedding_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE query_embedding_cache FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON query_embedding_cache USING(user_id=public.itemtalk_actor())
 WITH CHECK(user_id=public.itemtalk_actor());
REVOKE ALL ON query_embedding_cache FROM PUBLIC,itemtalk_app,itemtalk_worker;
GRANT SELECT,INSERT,UPDATE,DELETE ON query_embedding_cache TO itemtalk_app,itemtalk_worker;
GRANT SELECT,DELETE ON query_embedding_cache TO itemtalk_guard;
CREATE POLICY cache_cleanup ON query_embedding_cache TO itemtalk_guard USING(true);
CREATE INDEX ix_query_cache_expiry ON query_embedding_cache(expires_at);
ALTER TABLE assistant_runs ADD COLUMN runtime_snapshot jsonb;
ALTER TABLE jobs ADD COLUMN runtime_snapshot jsonb;

-- 保留原函数签名和领取栅栏。暂停只影响新顶层业务任务，显式子任务仍由父租约保护。
CREATE OR REPLACE FUNCTION itemtalk_queue_claim(requested uuid,lease_seconds integer) RETURNS SETOF public.jobs
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE selected public.jobs;
BEGIN
 IF lease_seconds NOT BETWEEN 1 AND 86400 THEN RAISE EXCEPTION 'Invalid lease'; END IF;
 SELECT * INTO selected FROM public.jobs WHERE status='queued' AND available_at<=now() AND attempts<max_attempts
 AND (requested IS NULL OR id=requested)
 AND (NOT COALESCE((SELECT background_paused FROM public.runtime_config WHERE singleton),false)
 OR job_type IN ('asset_cleanup','user_cleanup','checkpoint_cleanup'))
 ORDER BY available_at,created_at FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN; END IF;
 UPDATE public.jobs SET status='running',attempts=attempts+1,lease_token=gen_random_uuid(),
 lease_until=now()+make_interval(secs=>lease_seconds),started_at=COALESCE(started_at,now())
 WHERE id=selected.id RETURNING * INTO selected;
 UPDATE public.public_builds SET status='running' WHERE job_id=selected.id;
 RETURN NEXT selected;
END $$;
