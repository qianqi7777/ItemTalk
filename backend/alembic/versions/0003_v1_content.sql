CREATE TABLE manuals (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 product_model_id uuid NOT NULL REFERENCES product_models,
 user_product_instance_id uuid, owner_user_id uuid,
 visibility varchar(20) NOT NULL CHECK (visibility IN ('public','private')),
 version integer NOT NULL CHECK (version > 0), is_current boolean NOT NULL DEFAULT false,
 status varchar(20) NOT NULL CHECK (status IN ('pending','generating','ready','partial','failed')),
 evidence_hash char(64) NOT NULL, job_id uuid REFERENCES jobs,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK ((visibility='public' AND owner_user_id IS NULL AND user_product_instance_id IS NULL)
   OR (visibility='private' AND owner_user_id IS NOT NULL AND user_product_instance_id IS NOT NULL)),
 FOREIGN KEY (user_product_instance_id,owner_user_id,product_model_id)
   REFERENCES user_product_instances(id,user_id,product_model_id)
);
CREATE UNIQUE INDEX uq_manual_public_version ON manuals(product_model_id,version) WHERE visibility='public';
CREATE UNIQUE INDEX uq_manual_private_version ON manuals(user_product_instance_id,version) WHERE visibility='private';
CREATE UNIQUE INDEX uq_manual_public_current ON manuals(product_model_id) WHERE visibility='public' AND is_current;
CREATE UNIQUE INDEX uq_manual_private_current ON manuals(user_product_instance_id) WHERE visibility='private' AND is_current;
CREATE UNIQUE INDEX uq_manual_public_evidence ON manuals(product_model_id,evidence_hash) WHERE visibility='public';

CREATE TABLE manual_sections (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), manual_id uuid NOT NULL REFERENCES manuals ON DELETE CASCADE,
 parent_id uuid, section_key varchar(120) NOT NULL, title varchar(200) NOT NULL,
 body text, sort_order integer NOT NULL CHECK (sort_order >= 0),
 generation_status varchar(20) NOT NULL CHECK (generation_status IN ('pending','generating','ready','partial','failed')),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (manual_id,section_key), UNIQUE (id,manual_id), CHECK (parent_id <> id),
 FOREIGN KEY (parent_id,manual_id) REFERENCES manual_sections(id,manual_id)
);
CREATE UNIQUE INDEX uq_manual_section_order ON manual_sections
 (manual_id,COALESCE(parent_id,'00000000-0000-0000-0000-000000000000'::uuid),sort_order);
CREATE TABLE manual_section_citations (
 manual_section_id uuid NOT NULL REFERENCES manual_sections ON DELETE CASCADE,
 knowledge_chunk_id uuid NOT NULL REFERENCES knowledge_chunks, rank smallint NOT NULL CHECK (rank > 0),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (manual_section_id,knowledge_chunk_id), UNIQUE (manual_section_id,rank)
);
CREATE INDEX ix_manual_citation_chunk ON manual_section_citations(knowledge_chunk_id);
CREATE TABLE message_feedback (
 message_id uuid NOT NULL, user_id uuid NOT NULL REFERENCES users,
 feedback_type varchar(20) NOT NULL CHECK (feedback_type IN ('helpful','incorrect')),
 note text CHECK (length(note) <= 2000),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (message_id,user_id), FOREIGN KEY (message_id,user_id) REFERENCES messages(id,user_id)
);
CREATE TABLE knowledge_gaps (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), product_model_id uuid NOT NULL REFERENCES product_models,
 question_summary text NOT NULL, normalized_hash char(64) NOT NULL,
 occurrence_count integer NOT NULL DEFAULT 1 CHECK (occurrence_count > 0),
 status varchar(20) NOT NULL DEFAULT 'open' CHECK (status IN ('open','researching','resolved','dismissed')),
 research_job_id uuid REFERENCES jobs, resolved_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (product_model_id,normalized_hash)
);

CREATE TRIGGER touch_manuals BEFORE UPDATE ON manuals FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER touch_manual_sections BEFORE UPDATE ON manual_sections FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER touch_message_feedback BEFORE UPDATE ON message_feedback FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER touch_knowledge_gaps BEFORE UPDATE ON knowledge_gaps FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE FUNCTION protect_ready_section() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.generation_status='ready' AND
    ROW(NEW.manual_id,NEW.parent_id,NEW.section_key,NEW.title,NEW.body,NEW.sort_order,NEW.generation_status)
    IS DISTINCT FROM
    ROW(OLD.manual_id,OLD.parent_id,OLD.section_key,OLD.title,OLD.body,OLD.sort_order,OLD.generation_status) THEN
   RAISE EXCEPTION 'Published section is immutable; create a new manual version';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER protect_section_content BEFORE UPDATE ON manual_sections FOR EACH ROW EXECUTE FUNCTION protect_ready_section();

CREATE FUNCTION protect_ready_citation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE section_id uuid;
BEGIN
 IF TG_OP <> 'INSERT' THEN
   section_id=OLD.manual_section_id;
   IF EXISTS (SELECT 1 FROM manual_sections WHERE id=section_id AND generation_status='ready') THEN
     RAISE EXCEPTION 'Published section citations are immutable';
   END IF;
 END IF;
 IF TG_OP <> 'DELETE' THEN
   section_id=NEW.manual_section_id;
   IF EXISTS (SELECT 1 FROM manual_sections WHERE id=section_id AND generation_status='ready') THEN
     RAISE EXCEPTION 'Published section citations are immutable';
   END IF;
   RETURN NEW;
 END IF;
 RETURN OLD;
END $$;
CREATE TRIGGER protect_section_citations BEFORE INSERT OR UPDATE OR DELETE ON manual_section_citations
 FOR EACH ROW EXECUTE FUNCTION protect_ready_citation();

CREATE FUNCTION check_section_cycle() RETURNS trigger LANGUAGE plpgsql AS $$
-- 同事务可建立多层树，提交时检查最终父链；UNION也能终止已形成的环。
DECLARE has_cycle boolean;
BEGIN
 WITH RECURSIVE ancestors AS (
   SELECT parent.id,parent.parent_id FROM manual_sections child JOIN manual_sections parent
     ON parent.id=child.parent_id WHERE child.id=NEW.id
   UNION
   SELECT parent.id,parent.parent_id FROM manual_sections parent JOIN ancestors a ON parent.id=a.parent_id
 ) SELECT EXISTS (SELECT 1 FROM ancestors WHERE id=NEW.id) INTO has_cycle;
 IF has_cycle THEN RAISE EXCEPTION 'Manual section cycle is not permitted'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER check_manual_tree AFTER INSERT OR UPDATE ON manual_sections
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_section_cycle();
