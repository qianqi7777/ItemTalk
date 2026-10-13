CREATE TABLE user_memories (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users,
 memory_type varchar(40) NOT NULL CHECK (memory_type IN ('preference','product_fact','resume_summary')),
 scope varchar(20) NOT NULL CHECK (scope IN ('global','instance')),
 user_product_instance_id uuid, source_message_id uuid,
 content text NOT NULL CHECK (length(content) BETWEEN 1 AND 2000), content_hash char(64) NOT NULL,
 consent_status varchar(20) NOT NULL DEFAULT 'pending' CHECK (consent_status IN ('pending','confirmed','revoked')),
 confirmed_at timestamptz, expires_at timestamptz, deleted_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK ((scope='global' AND user_product_instance_id IS NULL AND memory_type='preference')
     OR (scope='instance' AND user_product_instance_id IS NOT NULL)),
 CHECK (consent_status <> 'confirmed' OR confirmed_at IS NOT NULL),
 FOREIGN KEY (source_message_id,user_id) REFERENCES messages(id,user_id),
 FOREIGN KEY (user_product_instance_id,user_id) REFERENCES user_product_instances(id,user_id),
 UNIQUE (user_id,source_message_id,memory_type,content_hash)
);
CREATE INDEX ix_user_memories_active ON user_memories(user_id,scope,user_product_instance_id) WHERE deleted_at IS NULL;
CREATE TRIGGER touch_user_memories BEFORE UPDATE ON user_memories FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
ALTER TABLE jobs DROP CONSTRAINT jobs_job_type_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_job_type_check CHECK
 (job_type IN ('identify','research','parse','embed','manual','asset_cleanup','user_cleanup','memory_extract'));

CREATE FUNCTION validate_memory_source() RETURNS trigger LANGUAGE plpgsql AS $$
-- 用户相同还不够：产品事实的来源消息必须属于同一产品实例。
BEGIN
 IF NEW.source_message_id IS NOT NULL AND NEW.scope='instance' AND NOT EXISTS (
   SELECT 1 FROM messages m JOIN conversations c ON c.id=m.conversation_id
   WHERE m.id=NEW.source_message_id AND m.user_id=NEW.user_id
     AND c.user_product_instance_id=NEW.user_product_instance_id
 ) THEN RAISE EXCEPTION 'Memory source must belong to its instance'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER check_memory_source BEFORE INSERT OR UPDATE ON user_memories
 FOR EACH ROW EXECUTE FUNCTION validate_memory_source();
