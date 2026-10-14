-- 有意不建 asset/user 外键：独立提交的对象预留即使主事务回滚也必须留下清理依据。
-- 正常写入由服务验证归属，RLS 仍限制本人；账号清理最后删除这些记录。
CREATE TABLE private_objects (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner_user_id uuid NOT NULL,
 asset_id uuid NOT NULL, object_key text UNIQUE NOT NULL,
 delete_after timestamptz NOT NULL, purged_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK (object_key ~ '^(temporary|private)/[a-f0-9]{32}$')
);
CREATE INDEX ix_private_object_asset ON private_objects(owner_user_id,asset_id);
ALTER TABLE private_objects ENABLE ROW LEVEL SECURITY;
ALTER TABLE private_objects FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON private_objects USING (owner_user_id=itemtalk_actor())
 WITH CHECK (owner_user_id=itemtalk_actor());
GRANT SELECT,INSERT,UPDATE,DELETE ON private_objects TO itemtalk_app,itemtalk_worker;

-- 迁移所有历史对象，包含确认后仍保存在临时清理任务里的旧 PUT 键。
-- 所有者也受 FORCE RLS 约束，迁移用固定临时策略，事务完成前移除。
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['assets','jobs','private_objects'] LOOP
  EXECUTE format('CREATE POLICY cleanup_backfill ON %I TO %I USING(true) WITH CHECK(true)', t,current_user);
 END LOOP;
END $$;
INSERT INTO private_objects(owner_user_id,asset_id,object_key,delete_after)
 SELECT owner_user_id,id,object_key,
  CASE WHEN object_key LIKE 'temporary/%' THEN GREATEST(COALESCE(expires_at,created_at+interval '15 minutes')+interval '10 minutes',created_at+interval '40 minutes') ELSE now() END
 FROM assets WHERE scope='private' AND object_key ~ '^(temporary|private)/[a-f0-9]{32}$'
 ON CONFLICT(object_key) DO NOTHING;
INSERT INTO private_objects(owner_user_id,asset_id,object_key,delete_after)
 SELECT user_id,(payload->>'assetId')::uuid,payload->>'temporaryKey',GREATEST(available_at,created_at+interval '40 minutes')
 FROM jobs WHERE job_type='asset_cleanup' AND user_id IS NOT NULL
  AND payload->>'temporaryKey' ~ '^temporary/[a-f0-9]{32}$'
 ON CONFLICT(object_key) DO NOTHING;
DROP POLICY cleanup_backfill ON assets;
DROP POLICY cleanup_backfill ON jobs;
DROP POLICY cleanup_backfill ON private_objects;
