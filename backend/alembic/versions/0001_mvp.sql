CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE users (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), login varchar(255) NOT NULL,
 login_normalized varchar(255) UNIQUE NOT NULL, display_name varchar(100),
 hashed_password varchar(1024) NOT NULL, is_admin boolean NOT NULL DEFAULT false,
 status varchar(20) NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled','deleting','deleted')),
 deleted_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE brands (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), name varchar(120) NOT NULL,
 normalized_name varchar(120) UNIQUE NOT NULL, official_domains jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE product_models (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), brand_id uuid NOT NULL REFERENCES brands,
 model_code varchar(160) NOT NULL, normalized_model_code varchar(160) NOT NULL,
 category varchar(80), region varchar(40), hardware_revision varchar(80),
 identity_status varchar(20) NOT NULL DEFAULT 'candidate' CHECK(identity_status IN ('candidate','user_confirmed','verified','rejected')),
 verification_status varchar(20) NOT NULL DEFAULT 'pending' CHECK(verification_status IN ('pending','verified','rejected')),
 verified_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK ((identity_status='verified')=(verification_status='verified') AND (identity_status<>'verified' OR verified_at IS NOT NULL))
);
CREATE UNIQUE INDEX uq_model_identity ON product_models(brand_id,normalized_model_code,COALESCE(region,''),COALESCE(hardware_revision,''));
CREATE TABLE product_aliases (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), product_model_id uuid NOT NULL REFERENCES product_models,
 alias varchar(160) NOT NULL, normalized_alias varchar(160) NOT NULL,
 alias_type varchar(30) CHECK(alias_type IN ('regional','legacy','common')), source_note text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(product_model_id,normalized_alias)
);
CREATE TABLE user_product_instances (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users,
 product_model_id uuid NOT NULL REFERENCES product_models, nickname varchar(120), serial_hmac varchar(128),
 privacy_level varchar(20), confirmed_at timestamptz, deleted_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,user_id), UNIQUE(id,user_id,product_model_id)
);
CREATE INDEX ix_instances_user ON user_product_instances(user_id,updated_at DESC) WHERE deleted_at IS NULL;
CREATE TABLE assets (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner_user_id uuid REFERENCES users,
 scope varchar(20) NOT NULL CHECK(scope IN ('private','public')), object_key text UNIQUE NOT NULL,
 sha256 char(64), mime_type varchar(100) NOT NULL, size_bytes bigint NOT NULL CHECK(size_bytes>0), kind varchar(30),
 status varchar(20) NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','available','processing','failed','deleting','deleted')),
 expires_at timestamptz, deleted_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,owner_user_id), UNIQUE(id,sha256,mime_type,scope),
 CHECK((scope='public' AND owner_user_id IS NULL) OR (scope='private' AND owner_user_id IS NOT NULL))
);
CREATE INDEX ix_assets_owner ON assets(owner_user_id,status,created_at);
CREATE TABLE sources (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), url text NOT NULL, normalized_url text UNIQUE NOT NULL,
 domain varchar(255), source_type varchar(20) CHECK(source_type IN ('official','authorized','community')),
 is_official boolean NOT NULL DEFAULT false,
 verification_status varchar(20) CHECK(verification_status IN ('pending','verified','rejected','revoked')),
 last_verified_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE documents (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), source_id uuid NOT NULL REFERENCES sources,
 logical_key varchar(200) NOT NULL, title text, language varchar(16),
 document_type varchar(30) CHECK(document_type IN ('manual','support','faq')),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(source_id,logical_key)
);
CREATE TABLE document_versions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), document_id uuid NOT NULL REFERENCES documents,
 asset_id uuid NOT NULL, asset_scope varchar(20) NOT NULL DEFAULT 'public' CHECK(asset_scope='public'),
 version_label varchar(80), sha256 char(64) NOT NULL, mime_type varchar(100) NOT NULL, published_on date,
 fetched_at timestamptz NOT NULL, parse_status varchar(20) CHECK(parse_status IN ('pending','processing','parsed','failed')),
 verification_status varchar(20) CHECK(verification_status IN ('pending','verified','rejected','revoked')),
 verified_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(document_id,sha256), FOREIGN KEY(asset_id,sha256,mime_type,asset_scope) REFERENCES assets(id,sha256,mime_type,scope)
);
CREATE TABLE document_pages (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), document_version_id uuid NOT NULL REFERENCES document_versions,
 page_no integer NOT NULL CHECK(page_no>=1), heading text, extracted_text text, locator jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(document_version_id,page_no)
);
CREATE TABLE document_model_links (
 document_version_id uuid NOT NULL REFERENCES document_versions, product_model_id uuid NOT NULL REFERENCES product_models,
 match_type varchar(20) CHECK(match_type IN ('exact','family')), confidence numeric(5,4),
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(document_version_id,product_model_id)
);
CREATE TABLE knowledge_chunks (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), document_version_id uuid NOT NULL REFERENCES document_versions,
 product_model_id uuid NOT NULL REFERENCES product_models, chunk_no integer NOT NULL CHECK(chunk_no>0),
 page_start integer NOT NULL CHECK(page_start>=1), page_end integer NOT NULL CHECK(page_end>=page_start),
 content text NOT NULL, content_type varchar(30) CHECK(content_type IN ('text','table','ocr')),
 verification_status varchar(20) CHECK(verification_status IN ('pending','verified','rejected','revoked')),
 embedding vector(1024), embedding_model varchar(120), embedding_revision varchar(40),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(document_version_id,product_model_id,chunk_no),
 FOREIGN KEY(document_version_id,product_model_id) REFERENCES document_model_links,
 CHECK((embedding IS NULL AND embedding_model IS NULL AND embedding_revision IS NULL) OR
 (embedding IS NOT NULL AND embedding_model IS NOT NULL AND embedding_revision IS NOT NULL))
);
CREATE INDEX ix_chunks_model ON knowledge_chunks(product_model_id,verification_status);
CREATE INDEX ix_chunks_text ON knowledge_chunks USING gin(content gin_trgm_ops);
CREATE TABLE conversations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users,
 user_product_instance_id uuid, title varchar(200), status varchar(20) CHECK(status IN ('active','archived')),
 deleted_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,user_id), FOREIGN KEY(user_product_instance_id,user_id) REFERENCES user_product_instances(id,user_id)
);
CREATE INDEX ix_conversations_user ON conversations(user_id,updated_at DESC);
CREATE TABLE assistant_runs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), conversation_id uuid NOT NULL REFERENCES conversations,
 user_id uuid NOT NULL REFERENCES users, status varchar(20) NOT NULL CHECK(status IN ('running','completed','interrupted','failed','cancelled')),
 idempotency_key varchar(160) NOT NULL, request_hash char(64) NOT NULL, model_id varchar(120), retry_of_run_id uuid,
 input_message_id uuid NOT NULL, heartbeat_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz NOT NULL,
 finished_at timestamptz, last_error text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,user_id), UNIQUE(id,conversation_id,user_id),
 FOREIGN KEY(conversation_id,user_id) REFERENCES conversations(id,user_id),
 FOREIGN KEY(retry_of_run_id,conversation_id,user_id) REFERENCES assistant_runs(id,conversation_id,user_id)
);
CREATE UNIQUE INDEX uq_run_key ON assistant_runs(conversation_id,idempotency_key);
CREATE UNIQUE INDEX uq_run_active ON assistant_runs(conversation_id) WHERE status='running';
CREATE TABLE messages (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), conversation_id uuid NOT NULL REFERENCES conversations,
 user_id uuid NOT NULL REFERENCES users, role varchar(20) NOT NULL CHECK(role IN ('user','assistant','system')),
 content text, model_id varchar(100), run_id uuid, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,user_id), UNIQUE(id,conversation_id,user_id),
 FOREIGN KEY(conversation_id,user_id) REFERENCES conversations(id,user_id),
 FOREIGN KEY(run_id,conversation_id,user_id) REFERENCES assistant_runs(id,conversation_id,user_id)
);
ALTER TABLE assistant_runs ADD CONSTRAINT fk_run_input FOREIGN KEY(input_message_id,conversation_id,user_id)
 REFERENCES messages(id,conversation_id,user_id) DEFERRABLE INITIALLY DEFERRED;
CREATE UNIQUE INDEX uq_run_output ON messages(run_id) WHERE role='assistant' AND run_id IS NOT NULL;
CREATE INDEX ix_messages_conversation ON messages(conversation_id,created_at,id);
CREATE TABLE message_attachments (
 message_id uuid NOT NULL, asset_id uuid NOT NULL, user_id uuid NOT NULL REFERENCES users,
 sort_order smallint NOT NULL CHECK(sort_order>=0), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(message_id,asset_id), UNIQUE(message_id,sort_order),
 FOREIGN KEY(message_id,user_id) REFERENCES messages(id,user_id), FOREIGN KEY(asset_id,user_id) REFERENCES assets(id,owner_user_id)
);
CREATE TABLE message_citations (
 message_id uuid NOT NULL REFERENCES messages, knowledge_chunk_id uuid NOT NULL REFERENCES knowledge_chunks,
 rank smallint NOT NULL CHECK(rank>0), display_meta jsonb, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(message_id,knowledge_chunk_id), UNIQUE(message_id,rank)
);
CREATE TABLE identification_attempts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users, input_text text,
 status varchar(30) CHECK(status IN ('queued','processing','waiting_for_user','confirmed','failed')), candidates jsonb,
 confirmed_product_model_id uuid REFERENCES product_models, confirmed_at timestamptz, correction_note text,
 supersedes_attempt_id uuid, clarification text, deleted_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,user_id),
 FOREIGN KEY(supersedes_attempt_id,user_id) REFERENCES identification_attempts(id,user_id)
);
CREATE TABLE identification_attempt_attachments (
 identification_attempt_id uuid NOT NULL, asset_id uuid NOT NULL, user_id uuid NOT NULL REFERENCES users,
 sort_order smallint NOT NULL CHECK(sort_order>=0), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(identification_attempt_id,asset_id), UNIQUE(identification_attempt_id,sort_order),
 FOREIGN KEY(identification_attempt_id,user_id) REFERENCES identification_attempts(id,user_id),
 FOREIGN KEY(asset_id,user_id) REFERENCES assets(id,owner_user_id)
);
CREATE TABLE jobs (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid REFERENCES users,
 job_type varchar(40) NOT NULL CHECK(job_type IN ('identify','research','parse','embed','manual','asset_cleanup','user_cleanup')),
 status varchar(30) NOT NULL CHECK(status IN ('queued','running','waiting_for_user','succeeded','partial_success','failed','cancelled')),
 idempotency_key varchar(160) NOT NULL, request_hash char(64) NOT NULL, retry_of_job_id uuid REFERENCES jobs,
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0), max_attempts integer NOT NULL DEFAULT 3 CHECK(max_attempts>=1),
 available_at timestamptz NOT NULL, lease_until timestamptz, lease_token uuid, started_at timestamptz, finished_at timestamptz,
 last_error text, payload jsonb NOT NULL, progress jsonb, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK(attempts<=max_attempts)
);
CREATE UNIQUE INDEX uq_job_user ON jobs(user_id,job_type,idempotency_key) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX uq_job_system ON jobs(job_type,idempotency_key) WHERE user_id IS NULL;
CREATE INDEX ix_queue ON jobs(available_at,created_at) WHERE status='queued';
CREATE INDEX ix_expired_lease ON jobs(lease_until) WHERE status='running';
CREATE TABLE public_builds (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), product_model_id uuid NOT NULL REFERENCES product_models,
 build_revision integer NOT NULL CHECK(build_revision>0), job_id uuid UNIQUE NOT NULL REFERENCES jobs,
 status varchar(30) NOT NULL CHECK(status IN ('queued','running','waiting_for_user','succeeded','partial_success','failed','cancelled')),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(product_model_id,build_revision)
);
CREATE UNIQUE INDEX uq_build_active ON public_builds(product_model_id) WHERE status IN ('queued','running','waiting_for_user');
CREATE TABLE job_request_links (
 job_id uuid NOT NULL REFERENCES jobs, user_id uuid NOT NULL REFERENCES users, user_product_instance_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(job_id,user_id,user_product_instance_id),
 FOREIGN KEY(user_product_instance_id,user_id) REFERENCES user_product_instances(id,user_id)
);
CREATE TABLE api_idempotency_records (
 user_id uuid NOT NULL REFERENCES users, operation varchar(200) NOT NULL, idempotency_key varchar(160) NOT NULL,
 request_hash char(64) NOT NULL, resource_id uuid NOT NULL, response_snapshot jsonb NOT NULL DEFAULT '{}',
 response_status smallint NOT NULL CHECK(response_status BETWEEN 200 AND 299), created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
 PRIMARY KEY(user_id,operation,idempotency_key), CHECK(jsonb_typeof(response_snapshot)='object')
);
CREATE TABLE job_events (
 id bigserial PRIMARY KEY, job_id uuid NOT NULL REFERENCES jobs,
 event_type varchar(30) NOT NULL CHECK(event_type IN ('status','progress','error','completed')),
 payload jsonb, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_events_job ON job_events(job_id,id);

-- Operational counters are separate from product evidence and private message content.
CREATE TABLE usage_counters (
 subject text NOT NULL, bucket text NOT NULL, amount integer NOT NULL DEFAULT 0,
 PRIMARY KEY(subject,bucket), CHECK(amount>=0)
);

CREATE FUNCTION touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at=now(); RETURN NEW; END $$;
DO $$ DECLARE t text; BEGIN
 FOR t IN SELECT table_name FROM information_schema.columns WHERE table_schema='public' AND column_name='updated_at'
 LOOP EXECUTE format('CREATE TRIGGER touch_%I BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION touch_updated_at()',t,t); END LOOP;
END $$;
CREATE FUNCTION validate_final_input() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent uuid; body text; has_attachment boolean; target text; parents uuid[];
BEGIN
 IF TG_TABLE_NAME IN ('messages','identification_attempts') THEN target=TG_TABLE_NAME; parents=ARRAY[NEW.id];
 ELSIF TG_TABLE_NAME='message_attachments' THEN
  target='messages';
  IF TG_OP='INSERT' THEN parents=ARRAY[NEW.message_id]; ELSIF TG_OP='DELETE' THEN parents=ARRAY[OLD.message_id]; ELSE parents=ARRAY[OLD.message_id,NEW.message_id]; END IF;
 ELSE
  target='identification_attempts';
  IF TG_OP='INSERT' THEN parents=ARRAY[NEW.identification_attempt_id]; ELSIF TG_OP='DELETE' THEN parents=ARRAY[OLD.identification_attempt_id]; ELSE parents=ARRAY[OLD.identification_attempt_id,NEW.identification_attempt_id]; END IF;
 END IF;
 FOR parent IN SELECT DISTINCT unnest(parents) ORDER BY 1 LOOP
  IF target='messages' THEN
   SELECT content INTO body FROM messages WHERE id=parent FOR UPDATE; IF NOT FOUND THEN CONTINUE; END IF;
   SELECT EXISTS(SELECT 1 FROM message_attachments WHERE message_id=parent) INTO has_attachment;
  ELSE
   SELECT input_text INTO body FROM identification_attempts WHERE id=parent FOR UPDATE; IF NOT FOUND THEN CONTINUE; END IF;
   SELECT EXISTS(SELECT 1 FROM identification_attempt_attachments WHERE identification_attempt_id=parent) INTO has_attachment;
  END IF;
  IF COALESCE(body,'') !~ '[^[:space:]]' AND NOT has_attachment THEN RAISE EXCEPTION 'Text or attachment required' USING ERRCODE='23514'; END IF;
 END LOOP;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER check_message_input AFTER INSERT OR UPDATE ON messages DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_final_input();
CREATE CONSTRAINT TRIGGER check_identification_input AFTER INSERT OR UPDATE ON identification_attempts DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_final_input();
CREATE CONSTRAINT TRIGGER check_message_attachment AFTER INSERT OR UPDATE OR DELETE ON message_attachments DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_final_input();
CREATE CONSTRAINT TRIGGER check_identification_attachment AFTER INSERT OR UPDATE OR DELETE ON identification_attempt_attachments DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_final_input();
