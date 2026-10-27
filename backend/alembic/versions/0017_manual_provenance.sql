ALTER TABLE document_versions ADD COLUMN official_parent_version_id uuid REFERENCES document_versions(id);
ALTER TABLE document_versions ADD COLUMN official_link_url text;
ALTER TABLE document_versions ADD CONSTRAINT official_link_pair CHECK ((official_parent_version_id IS NULL)=(official_link_url IS NULL));
ALTER TABLE document_versions ADD CONSTRAINT official_link_shape CHECK (official_parent_version_id IS NULL OR (official_parent_version_id<>id AND mime_type='application/pdf' AND length(official_link_url) BETWEEN 1 AND 2048));
CREATE INDEX ix_document_official_parent ON document_versions(official_parent_version_id) WHERE official_parent_version_id IS NOT NULL;

CREATE FUNCTION itemtalk_manual_link_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_OP='UPDATE' AND (OLD.official_parent_version_id IS DISTINCT FROM NEW.official_parent_version_id OR OLD.official_link_url IS DISTINCT FROM NEW.official_link_url) THEN
  RAISE EXCEPTION 'Manual provenance is immutable';
 END IF;
 IF NEW.official_parent_version_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.document_versions p WHERE p.id=NEW.official_parent_version_id AND p.mime_type='text/html' AND p.official_parent_version_id IS NULL) THEN
  RAISE EXCEPTION 'Manual parent must be a direct HTML version';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER manual_link_immutable BEFORE INSERT OR UPDATE ON document_versions FOR EACH ROW EXECUTE FUNCTION itemtalk_manual_link_immutable();

CREATE FUNCTION itemtalk_revoke_linked_manuals() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE affected uuid[];
BEGIN
 IF NEW.verification_status NOT IN ('revoked','rejected') OR OLD.verification_status IS NOT DISTINCT FROM NEW.verification_status THEN RETURN NEW; END IF;
 IF TG_TABLE_NAME='document_versions' THEN
  IF NEW.official_parent_version_id IS NOT NULL THEN
   UPDATE public.sources SET verification_status='revoked',is_official=false WHERE id=(SELECT source_id FROM public.documents WHERE id=NEW.document_id) AND verification_status NOT IN ('revoked','rejected');
   UPDATE public.knowledge_chunks SET verification_status='revoked' WHERE document_version_id=NEW.id;
  END IF;
 END IF;
 IF TG_TABLE_NAME='sources' THEN
  SELECT array_agg(v.id) INTO affected FROM public.document_versions v JOIN public.document_versions p ON p.id=v.official_parent_version_id JOIN public.documents d ON d.id=p.document_id WHERE d.source_id=NEW.id;
 ELSE
  SELECT array_agg(v.id) INTO affected FROM public.document_versions v WHERE v.official_parent_version_id=NEW.id;
 END IF;
 IF affected IS NULL THEN RETURN NEW; END IF;
 -- Conservatively withdraw the CDN URL, including historical citation labels.
 UPDATE public.sources SET verification_status='revoked',is_official=false WHERE id IN (SELECT d.source_id FROM public.documents d JOIN public.document_versions v ON v.document_id=d.id WHERE v.id=ANY(affected)) AND verification_status NOT IN ('revoked','rejected');
 UPDATE public.document_versions SET verification_status='revoked' WHERE id=ANY(affected) AND verification_status NOT IN ('revoked','rejected');
 UPDATE public.knowledge_chunks SET verification_status='revoked' WHERE document_version_id=ANY(affected);
 RETURN NEW;
END $$;
CREATE TRIGGER source_link_withdrawal AFTER UPDATE OF verification_status ON sources FOR EACH ROW EXECUTE FUNCTION itemtalk_revoke_linked_manuals();
CREATE TRIGGER version_link_withdrawal AFTER UPDATE OF verification_status ON document_versions FOR EACH ROW EXECUTE FUNCTION itemtalk_revoke_linked_manuals();
REVOKE ALL ON FUNCTION itemtalk_manual_link_immutable(),itemtalk_revoke_linked_manuals() FROM PUBLIC;
