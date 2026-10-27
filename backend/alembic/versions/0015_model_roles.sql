ALTER TABLE model_configs ADD COLUMN role varchar(16) NOT NULL DEFAULT 'chat'
 CHECK(role IN ('chat','vision','stt','embedding'));
ALTER TABLE model_configs ADD COLUMN embedding_revision varchar(40);
ALTER TABLE model_configs ADD COLUMN dimensions integer;
ALTER TABLE model_configs DROP CONSTRAINT model_configs_protocol_check;
ALTER TABLE model_configs ADD CONSTRAINT model_configs_role_protocol_check CHECK(
 (role IN ('chat','vision') AND protocol IN ('openai_chat','dashscope_chat')) OR
 (role='stt' AND protocol IN ('dashscope_audio','openai_transcription')) OR
 (role='embedding' AND protocol IN ('openai_embeddings','dashscope_embeddings'))
);
ALTER TABLE model_configs ADD CONSTRAINT model_configs_embedding_identity_check CHECK(
 (role='embedding' AND embedding_revision IS NOT NULL AND dimensions IS NOT NULL AND dimensions=1024) OR
 (role<>'embedding' AND embedding_revision IS NULL AND dimensions IS NULL)
);
ALTER TABLE runtime_config ADD COLUMN vision_profile varchar(64) NOT NULL DEFAULT 'current';
ALTER TABLE runtime_config ADD COLUMN stt_profile varchar(64) NOT NULL DEFAULT 'current';
ALTER TABLE runtime_config ADD COLUMN embedding_profile varchar(64) NOT NULL DEFAULT 'current';

CREATE FUNCTION itemtalk_role_model_config_list() RETURNS jsonb
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF NOT public.itemtalk_is_admin() THEN RAISE EXCEPTION 'Administrator required'; END IF;
 RETURN (SELECT coalesce(jsonb_agg(to_jsonb(c)), '[]'::jsonb) FROM
  (SELECT id,name,base_url,protocol,model,created_at,role,embedding_revision,dimensions
   FROM public.model_configs ORDER BY created_at DESC,id LIMIT 100) c);
END $$;
ALTER FUNCTION itemtalk_role_model_config_list() OWNER TO itemtalk_guard;
REVOKE ALL ON FUNCTION itemtalk_role_model_config_list() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION itemtalk_role_model_config_list() TO itemtalk_app,itemtalk_migrator;

-- Preserve the pre-0015 function's text-only contract for application rollback.
CREATE OR REPLACE FUNCTION itemtalk_model_config_list() RETURNS jsonb
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF NOT public.itemtalk_is_admin() THEN RAISE EXCEPTION 'Administrator required'; END IF;
 RETURN (SELECT coalesce(jsonb_agg(to_jsonb(c)), '[]'::jsonb) FROM
  (SELECT id,name,base_url,protocol,model,created_at FROM public.model_configs WHERE role='chat'
   ORDER BY created_at DESC,id LIMIT 100) c);
END $$;

CREATE FUNCTION itemtalk_add_role_model_config(profile text,label text,base text,wire text,
 model_name text,secret text,model_role text,rev text,dims integer)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE changed public.model_configs; current_version integer;
BEGIN
 IF NOT public.itemtalk_is_admin() THEN RAISE EXCEPTION 'Administrator required'; END IF;
 SELECT version INTO current_version FROM public.runtime_config WHERE singleton FOR UPDATE;
 IF (SELECT count(*) FROM public.model_configs)>=100 THEN RAISE EXCEPTION 'Model configuration limit reached'; END IF;
 INSERT INTO public.model_configs(id,name,base_url,protocol,model,encrypted_key,created_by,role,embedding_revision,dimensions)
 VALUES(profile,label,base,wire,model_name,secret,public.itemtalk_actor(),model_role,rev,dims) RETURNING * INTO changed;
 INSERT INTO public.runtime_config_audit(actor_id,version,before_value,after_value)
 VALUES(public.itemtalk_actor(),current_version,'{}',jsonb_build_object(
  'action','create_model_config','profile',profile,'name',label,'base_url',base,'protocol',wire,
  'model',model_name,'role',model_role,'embedding_revision',rev,'dimensions',dims));
 RETURN to_jsonb(changed)-'encrypted_key'-'created_by';
END $$;
ALTER FUNCTION itemtalk_add_role_model_config(text,text,text,text,text,text,text,text,integer) OWNER TO itemtalk_guard;
REVOKE ALL ON FUNCTION itemtalk_add_role_model_config(text,text,text,text,text,text,text,text,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION itemtalk_add_role_model_config(text,text,text,text,text,text,text,text,integer) TO itemtalk_app,itemtalk_migrator;

CREATE FUNCTION itemtalk_update_role_runtime(expected integer,g integer,s integer,paused boolean,
 chat text,vision text,speech text,embedding text)
 RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE old public.runtime_config; changed public.runtime_config; pair text[];
BEGIN
 IF NOT public.itemtalk_is_admin() THEN RAISE EXCEPTION 'Administrator required'; END IF;
 SELECT * INTO old FROM public.runtime_config WHERE singleton FOR UPDATE;
 IF old.version<>expected THEN RETURN NULL; END IF;
 FOREACH pair SLICE 1 IN ARRAY ARRAY[
  ARRAY['chat',chat], ARRAY['vision',coalesce(vision,old.vision_profile)],
  ARRAY['stt',coalesce(speech,old.stt_profile)], ARRAY['embedding',coalesce(embedding,old.embedding_profile)]
 ] LOOP
  IF pair[2] IS NULL OR length(pair[2])>64 OR pair[2] !~ '^[a-zA-Z0-9_-]+$' THEN
   RAISE EXCEPTION 'Invalid profile';
  END IF;
  IF pair[2] LIKE 'managed\_%' ESCAPE '\' AND NOT EXISTS(
   SELECT 1 FROM public.model_configs WHERE id=pair[2] AND role=pair[1]) THEN
   RAISE EXCEPTION 'Model role mismatch';
  END IF;
  IF pair[1]<>'chat' AND pair[2]<>'current' AND pair[2] NOT LIKE 'managed\_%' ESCAPE '\' THEN
   RAISE EXCEPTION 'Invalid role profile';
  END IF;
 END LOOP;
 UPDATE public.runtime_config SET version=version+1,global_limit=g,subject_limit=s,
 background_paused=paused,chat_profile=chat,vision_profile=coalesce(vision,old.vision_profile),
 stt_profile=coalesce(speech,old.stt_profile),embedding_profile=coalesce(embedding,old.embedding_profile),
 updated_at=now() WHERE singleton RETURNING * INTO changed;
 INSERT INTO public.runtime_config_audit(actor_id,version,before_value,after_value)
 VALUES(public.itemtalk_actor(),changed.version,to_jsonb(old),to_jsonb(changed));
 RETURN to_jsonb(changed);
END $$;
ALTER FUNCTION itemtalk_update_role_runtime(integer,integer,integer,boolean,text,text,text,text) OWNER TO itemtalk_guard;
REVOKE ALL ON FUNCTION itemtalk_update_role_runtime(integer,integer,integer,boolean,text,text,text,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION itemtalk_update_role_runtime(integer,integer,integer,boolean,text,text,text,text) TO itemtalk_app,itemtalk_migrator;

CREATE OR REPLACE FUNCTION itemtalk_update_runtime(expected integer,g integer,s integer,paused boolean,profile text)
 RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT public.itemtalk_update_role_runtime(expected,g,s,paused,profile,NULL,NULL,NULL)
$$;
