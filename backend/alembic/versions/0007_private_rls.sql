-- 角色须先由管理员执行 infra/runtime-roles.sql 建立；迁移账号不能创建登录角色。
-- FORCE 也约束表所有者。守卫为 NOLOGIN/NOBYPASSRLS，只能经固定函数访问白名单表。
CREATE FUNCTION itemtalk_actor() RETURNS uuid LANGUAGE sql STABLE
 SET search_path=pg_catalog AS $$ SELECT NULLIF(current_setting('itemtalk.user_id',true),'')::uuid $$;

DO $$ DECLARE t text; expression text; BEGIN
 FOREACH t IN ARRAY ARRAY['users','user_product_instances','assets','conversations','assistant_runs',
   'messages','message_attachments','message_citations','identification_attempts',
   'identification_attempt_attachments','jobs','job_request_links','api_idempotency_records',
   'job_events','manuals','manual_sections','manual_section_citations','message_feedback',
   'user_memories','guidance_sessions','guidance_steps','guidance_step_citations',
   'guidance_step_attachments','checkpoints','checkpoint_blobs','checkpoint_writes'] LOOP
  expression=CASE
   WHEN t='users' THEN 'id=public.itemtalk_actor()'
   WHEN t='assets' THEN 'scope=''public'' OR owner_user_id=public.itemtalk_actor()'
   WHEN t='jobs' THEN 'user_id IS NULL OR user_id=public.itemtalk_actor()'
   WHEN t='message_citations' THEN 'EXISTS(SELECT 1 FROM public.messages p WHERE p.id=message_id)'
   WHEN t='job_events' THEN 'EXISTS(SELECT 1 FROM public.jobs p WHERE p.id=job_id)'
   WHEN t='manuals' THEN 'visibility=''public'' OR owner_user_id=public.itemtalk_actor()'
   WHEN t='manual_sections' THEN 'EXISTS(SELECT 1 FROM public.manuals p WHERE p.id=manual_id)'
   WHEN t='manual_section_citations' THEN 'EXISTS(SELECT 1 FROM public.manual_sections p WHERE p.id=manual_section_id)'
   WHEN t='guidance_step_citations' THEN 'EXISTS(SELECT 1 FROM public.guidance_steps p WHERE p.id=guidance_step_id)'
   WHEN t IN ('checkpoints','checkpoint_blobs','checkpoint_writes') THEN
    'split_part(thread_id,'':'',1) IN (''probe'',''guidance'',''guidance-plan'') AND split_part(thread_id,'':'',2)=public.itemtalk_actor()::text'
   ELSE 'user_id=public.itemtalk_actor()' END;
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant_isolation ON public.%I USING (%s) WITH CHECK (%s)',t,expression,expression);
 END LOOP;
END $$;

-- 私有表只从父行继承可见性；守卫角色的策略不授予 API/Worker，亦不允许 SET ROLE。
CREATE POLICY auth_guard ON users TO itemtalk_guard USING (true) WITH CHECK (true);
CREATE POLICY queue_guard ON jobs TO itemtalk_guard USING (true) WITH CHECK (true);
CREATE POLICY recovery_guard ON assistant_runs TO itemtalk_guard USING (true) WITH CHECK (true);
GRANT SELECT,INSERT ON users TO itemtalk_guard;
GRANT SELECT,UPDATE ON jobs,assistant_runs,public_builds TO itemtalk_guard;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO itemtalk_app,itemtalk_worker;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO itemtalk_app,itemtalk_worker;
REVOKE INSERT,UPDATE ON users FROM itemtalk_app,itemtalk_worker;
GRANT UPDATE(display_name,status,deleted_at,updated_at) ON users TO itemtalk_app,itemtalk_worker;
-- TRUNCATE 不受 RLS 控制，运行角色只能使用逐行的 DELETE。
REVOKE TRUNCATE,REFERENCES,TRIGGER ON ALL TABLES IN SCHEMA public FROM itemtalk_app,itemtalk_worker;

CREATE FUNCTION itemtalk_register(email text,normalized text,password_hash text)
 RETURNS TABLE(id uuid,login varchar,display_name varchar) LANGUAGE sql SECURITY DEFINER
 SET search_path=pg_catalog,public AS $$
 INSERT INTO public.users(login,login_normalized,hashed_password)
 VALUES(email,normalized,password_hash) ON CONFLICT(login_normalized) DO NOTHING
 RETURNING users.id,users.login,users.display_name
$$;
CREATE FUNCTION itemtalk_login(normalized text) RETURNS TABLE(id uuid,hashed_password varchar)
 LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
 SELECT u.id,u.hashed_password FROM public.users u
 WHERE u.login_normalized=normalized AND u.status='active' AND u.deleted_at IS NULL
$$;
ALTER FUNCTION itemtalk_register(text,text,text) OWNER TO itemtalk_guard;
ALTER FUNCTION itemtalk_login(text) OWNER TO itemtalk_guard;
REVOKE ALL ON FUNCTION itemtalk_register(text,text,text),itemtalk_login(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION itemtalk_register(text,text,text),itemtalk_login(text) TO itemtalk_app;

CREATE FUNCTION itemtalk_queue_recover(timeout_seconds integer) RETURNS void
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF timeout_seconds NOT BETWEEN 1 AND 86400 THEN RAISE EXCEPTION 'Invalid timeout'; END IF;
 WITH expired AS (
  UPDATE public.jobs SET status=CASE WHEN attempts<max_attempts THEN 'queued' ELSE 'failed' END,
   lease_token=NULL,lease_until=NULL,available_at=now(),last_error='任务租约超时',
   finished_at=CASE WHEN attempts>=max_attempts THEN now() ELSE NULL END
  WHERE status='running' AND lease_until<now() RETURNING id,status
 ) UPDATE public.public_builds b SET status=e.status FROM expired e WHERE b.job_id=e.id;
 UPDATE public.assistant_runs SET status='interrupted',finished_at=now(),last_error='回答连接超时'
 WHERE status='running' AND heartbeat_at<now()-make_interval(secs=>timeout_seconds);
END $$;
CREATE FUNCTION itemtalk_queue_claim(requested uuid,lease_seconds integer) RETURNS SETOF public.jobs
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE selected public.jobs;
BEGIN
 IF lease_seconds NOT BETWEEN 1 AND 86400 THEN RAISE EXCEPTION 'Invalid lease'; END IF;
 SELECT * INTO selected FROM public.jobs
 WHERE status='queued' AND available_at<=now() AND attempts<max_attempts
   AND (requested IS NULL OR id=requested) ORDER BY available_at,created_at
 FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN; END IF;
 UPDATE public.jobs SET status='running',attempts=attempts+1,lease_token=gen_random_uuid(),
  lease_until=now()+make_interval(secs=>lease_seconds),started_at=COALESCE(started_at,now())
 WHERE id=selected.id RETURNING * INTO selected;
 UPDATE public.public_builds SET status='running' WHERE job_id=selected.id;
 RETURN NEXT selected;
END $$;
ALTER FUNCTION itemtalk_queue_recover(integer) OWNER TO itemtalk_guard;
ALTER FUNCTION itemtalk_queue_claim(uuid,integer) OWNER TO itemtalk_guard;
REVOKE ALL ON FUNCTION itemtalk_queue_recover(integer),itemtalk_queue_claim(uuid,integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION itemtalk_queue_recover(integer),itemtalk_queue_claim(uuid,integer) TO itemtalk_worker;
