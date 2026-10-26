-- 密钥仅存独立主密钥加密后的密文；不可变ID供API和Worker的历史快照解析。
CREATE TABLE model_configs (
 id varchar(64) PRIMARY KEY CHECK(id ~ '^managed_[a-f0-9]{32}$'),
 name varchar(80) NOT NULL CHECK(length(name)>0),
 base_url varchar(500) NOT NULL CHECK(base_url LIKE 'https://%'),
 protocol varchar(30) NOT NULL CHECK(protocol IN ('openai_chat','dashscope_chat')),
 model varchar(100) NOT NULL CHECK(length(model)>0),
 encrypted_key text NOT NULL CHECK(length(encrypted_key) BETWEEN 80 AND 8192),
 created_by uuid REFERENCES users(id) ON DELETE SET NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE model_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_configs FORCE ROW LEVEL SECURITY;
CREATE POLICY model_configs_guard ON model_configs TO itemtalk_guard USING(true) WITH CHECK(true);
REVOKE ALL ON model_configs FROM PUBLIC,itemtalk_app,itemtalk_worker;
GRANT SELECT,INSERT ON model_configs TO itemtalk_guard;

CREATE FUNCTION itemtalk_model_config(profile text) RETURNS jsonb
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT to_jsonb(c) FROM public.model_configs c WHERE id=profile
$$;
ALTER FUNCTION itemtalk_model_config(text) OWNER TO itemtalk_guard;
REVOKE ALL ON FUNCTION itemtalk_model_config(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION itemtalk_model_config(text) TO itemtalk_app,itemtalk_worker,itemtalk_migrator;

CREATE FUNCTION itemtalk_model_config_list() RETURNS jsonb
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF NOT public.itemtalk_is_admin() THEN RAISE EXCEPTION 'Administrator required'; END IF;
 RETURN (SELECT coalesce(jsonb_agg(to_jsonb(c)), '[]'::jsonb) FROM
  (SELECT id,name,base_url,protocol,model,created_at FROM public.model_configs
   ORDER BY created_at DESC,id LIMIT 100) c);
END $$;
ALTER FUNCTION itemtalk_model_config_list() OWNER TO itemtalk_guard;
REVOKE ALL ON FUNCTION itemtalk_model_config_list() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION itemtalk_model_config_list() TO itemtalk_app,itemtalk_migrator;

CREATE FUNCTION itemtalk_add_model_config(profile text,label text,base text,wire text,model_name text,secret text)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE changed public.model_configs; current_version integer;
BEGIN
 IF NOT public.itemtalk_is_admin() THEN RAISE EXCEPTION 'Administrator required'; END IF;
 SELECT version INTO current_version FROM public.runtime_config WHERE singleton FOR UPDATE;
 IF (SELECT count(*) FROM public.model_configs)>=100 THEN RAISE EXCEPTION 'Model configuration limit reached'; END IF;
 INSERT INTO public.model_configs(id,name,base_url,protocol,model,encrypted_key,created_by)
 VALUES(profile,label,base,wire,model_name,secret,public.itemtalk_actor()) RETURNING * INTO changed;
 INSERT INTO public.runtime_config_audit(actor_id,version,before_value,after_value)
 VALUES(public.itemtalk_actor(),current_version,'{}',jsonb_build_object(
  'action','create_model_config','profile',profile,'name',label,'base_url',base,'protocol',wire,'model',model_name));
 RETURN to_jsonb(changed)-'encrypted_key'-'created_by';
END $$;
ALTER FUNCTION itemtalk_add_model_config(text,text,text,text,text,text) OWNER TO itemtalk_guard;
REVOKE ALL ON FUNCTION itemtalk_add_model_config(text,text,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION itemtalk_add_model_config(text,text,text,text,text,text) TO itemtalk_app,itemtalk_migrator;
