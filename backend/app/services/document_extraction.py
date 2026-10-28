"""Checkpoint successful scanned-page transcriptions before complete publication."""

import hashlib
import json
import time

from app.adapters import ModelGateway
from app.db import execute, json_value, many, one, transaction
from app.deadlines import request_scope
from app.document_ocr import transcribe_page
from app.errors import DomainError
from app.pdf_render import render_page
from app.services.queue import progress


def extract_scanned_pages(job, document, data, native_pages, scanned_pages, validate_authority):
    with request_scope(time.monotonic() + 600):
        try:
            return _extract(job, document, data, native_pages, scanned_pages, validate_authority)
        except DomainError as error:
            if error.code != "ANSWER_TIMEOUT":
                raise
            raise DomainError(
                504, "UPSTREAM_MODEL_ERROR", "扫描页识别超过 10 分钟，已保存成功页面，请重试"
            ) from None


def _extract(job, document, data, native_pages, scanned_pages, validate_authority):
    if len(scanned_pages) > 20:
        raise DomainError(422, "SOURCE_UNVERIFIED", "扫描页超过单份说明书 20 页识别上限")
    gateway = ModelGateway()
    fingerprint = hashlib.sha256(
        json.dumps(
            [
                gateway.config.vision_provider,
                gateway.config.vision_base_url,
                gateway.config.vision_model,
            ]
        ).encode()
    ).hexdigest()
    try:
        with transaction() as connection:
            validate_authority(connection)
            cached = {
                row["page_no"]: row["extracted_text"]
                for row in many(
                    connection,
                    "SELECT page_no,extracted_text FROM document_pages "
                    "WHERE document_version_id=:v "
                    "AND locator->>'extractionMethod'='vision_ocr' AND locator->>'complete'='true' "
                    "AND locator->>'configFingerprint'=:fingerprint",
                    v=document["id"],
                    fingerprint=fingerprint,
                )
            }
    except BaseException:
        gateway.close()
        raise
    output = dict(native_pages)
    try:
        for index, page in enumerate(scanned_pages):
            if page in cached:
                output[page] = cached[page]
                continue
            with transaction() as connection:
                validate_authority(connection)
            progress(job, "recognizing_scanned_pages", 10 + int(index / len(scanned_pages) * 80))
            image = render_page(data, page)
            text = transcribe_page(gateway, image, page, job["user_id"])
            if output[page].strip() and not text:
                raise DomainError(422, "SOURCE_UNVERIFIED", "扫描页识别遗漏已有文字，请重试")
            if (
                sum(len(value) for value in output.values()) - len(output[page]) + len(text)
                > 2_000_000
            ):
                raise DomainError(422, "SOURCE_UNVERIFIED", "扫描页识别正文超过输出限制")
            with transaction() as connection:
                validate_authority(connection)
                execute(
                    connection,
                    "INSERT INTO document_pages"
                    "(document_version_id,page_no,extracted_text,locator) "
                    "VALUES(:v,:p,:text,CAST(:locator AS jsonb)) "
                    "ON CONFLICT(document_version_id,page_no) "
                    "DO UPDATE SET extracted_text=EXCLUDED.extracted_text,locator=EXCLUDED.locator "
                    "WHERE document_pages.locator->>'configFingerprint' "
                    "IS DISTINCT FROM :fingerprint",
                    v=document["id"],
                    p=page,
                    text=text,
                    fingerprint=fingerprint,
                    locator=json_value(
                        {
                            "extractionMethod": "vision_ocr",
                            "model": gateway.config.vision_model,
                            "configFingerprint": fingerprint,
                            "complete": True,
                        }
                    ),
                )
                # A concurrent valid worker may have completed this same immutable page.
                # Verify and publish the winning stored text, never the losing model response.
                text = one(
                    connection,
                    "SELECT extracted_text FROM document_pages WHERE document_version_id=:v "
                    "AND page_no=:p",
                    v=document["id"],
                    p=page,
                )["extracted_text"]
            output[page] = text
    finally:
        gateway.close()
    if not any(text.strip() for text in output.values()):
        raise DomainError(422, "SOURCE_UNVERIFIED", "扫描页识别完成，但没有可用文字")
    if sum(len(text) for text in output.values()) > 2_000_000:
        raise DomainError(422, "SOURCE_UNVERIFIED", "扫描页识别正文超过输出限制")
    return sorted(output.items())
