ALTER TABLE manuals ADD COLUMN generation_profile varchar(40) NOT NULL DEFAULT 'legacy';
ALTER TABLE manuals ADD COLUMN context_snapshot jsonb NOT NULL DEFAULT '[]'
 CHECK(jsonb_typeof(context_snapshot)='array');
ALTER TABLE manuals ADD COLUMN context_hash char(64);
ALTER TABLE manuals ADD CONSTRAINT public_manual_has_no_personal_context
 CHECK(visibility='private' OR (context_snapshot='[]'::jsonb AND context_hash IS NULL));
CREATE UNIQUE INDEX uq_manual_private_inputs ON manuals(user_product_instance_id,evidence_hash)
 WHERE visibility='private' AND generation_profile<>'legacy';
ALTER TABLE manual_sections ADD COLUMN evidence_ids uuid[] NOT NULL DEFAULT '{}';
ALTER TABLE manual_sections ADD COLUMN generation_prompt text;
ALTER TABLE manual_sections ADD COLUMN input_hash char(64);
ALTER TABLE manual_sections ADD COLUMN source_section_id uuid REFERENCES manual_sections ON DELETE SET NULL;
ALTER TABLE manual_sections ADD COLUMN personal_notes jsonb NOT NULL DEFAULT '[]'
 CHECK(jsonb_typeof(personal_notes)='array');

CREATE OR REPLACE FUNCTION protect_ready_section() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.generation_status='ready' AND
    ROW(NEW.manual_id,NEW.parent_id,NEW.section_key,NEW.title,NEW.body,NEW.sort_order,
        NEW.generation_status,NEW.evidence_ids,NEW.generation_prompt,NEW.input_hash,NEW.personal_notes)
    IS DISTINCT FROM
    ROW(OLD.manual_id,OLD.parent_id,OLD.section_key,OLD.title,OLD.body,OLD.sort_order,
        OLD.generation_status,OLD.evidence_ids,OLD.generation_prompt,OLD.input_hash,OLD.personal_notes) THEN
   RAISE EXCEPTION 'Published section inputs and content are immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION private_section_notes_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.personal_notes<>'[]'::jsonb AND EXISTS(
   SELECT 1 FROM manuals WHERE id=NEW.manual_id AND visibility='public'
 ) THEN RAISE EXCEPTION 'Personal notes cannot enter public manuals'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER check_section_note_scope BEFORE INSERT OR UPDATE ON manual_sections
 FOR EACH ROW EXECUTE FUNCTION private_section_notes_only();
CREATE FUNCTION protect_manual_inputs() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.product_model_id,NEW.owner_user_id,NEW.user_product_instance_id,NEW.visibility,
        NEW.version,NEW.evidence_hash,NEW.generation_profile,NEW.context_snapshot,NEW.context_hash)
    IS DISTINCT FROM
    ROW(OLD.product_model_id,OLD.owner_user_id,OLD.user_product_instance_id,OLD.visibility,
        OLD.version,OLD.evidence_hash,OLD.generation_profile,OLD.context_snapshot,OLD.context_hash) THEN
   RAISE EXCEPTION 'Manual generation inputs are immutable; create a new version';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER protect_manual_generation BEFORE UPDATE ON manuals
 FOR EACH ROW EXECUTE FUNCTION protect_manual_inputs();
