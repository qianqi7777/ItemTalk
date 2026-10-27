"""Private observe -> retrieve real references -> visually compare pipeline."""

import time
from uuid import uuid4

from app.adapters import SearchGateway
from app.deadlines import request_scope
from app.errors import DomainError
from app.identification import labeled_value, normalized
from app.recognition_models import Comparison, Observation, PhotoRequest
from app.recognition_vision import compare, observe
from app.services.recognition_sources import (
    APPLE_ALIASES,
    exact_image_title,
    fetch_references,
    search_queries,
    supports_model,
)
from app.services.web_research import BRANDS, official_url


def brand_domains(brand, registered=None):
    from app.security import normalize

    name = normalize(brand or "")
    if registered and registered.get(name):
        return registered[name]
    if name in APPLE_ALIASES:
        return ["apple.com", "apple.com.cn"]
    return list(next((domains for aliases, domains in BRANDS if name in aliases), ()))


def candidates_from_sources(comparison, references, previous, notes, registered_domains=None):
    by_id = {row["id"]: row for row in references}
    rejected = {identity_key(value) for value in previous.get("rejectedModels", [])}
    result, seen = [], set()
    for item in comparison.candidates:
        identity = identity_key(item.brandName + item.modelCode)
        if identity in rejected or identity in seen:
            continue
        matched = [
            by_id[key]
            for key in item.sourceIds
            if key in by_id
            and supports_model(
                item.brandName,
                item.modelCode,
                by_id[key]["text"],
                trusted_brand=official_url(
                    by_id[key]["url"], brand_domains(item.brandName, registered_domains)
                ),
            )
        ]
        if not matched:
            continue
        # A generated reference id or an unrelated thumbnail must never become a source.
        image = next(
            (
                row.get("imageData")
                for row in matched
                if row["id"] == item.referenceImageSourceId
                and exact_image_title(row["title"], item.brandName, item.modelCode)
            ),
            None,
        )
        result.append(
            {
                "candidateId": str(uuid4()),
                "brandName": item.brandName,
                "modelCode": item.modelCode,
                "region": item.region if labeled_value(item.region, (notes,)) else None,
                "hardwareRevision": item.hardwareRevision
                if labeled_value(item.hardwareRevision, (notes,), hardware=True)
                else None,
                "basis": "appearance",
                "confidence": 0.6,
                "strength": item.strength,
                "evidence": "；".join(item.matchReasons)[:1000],
                "matchReasons": item.matchReasons,
                "differences": item.differences,
                "missingEvidence": item.missingEvidence,
                "referenceImage": image,
                "sources": [
                    {key: row[key] for key in ("id", "url", "title", "sourceKind")}
                    for row in matched
                ],
            }
        )
        seen.add(identity)
    return result[:3]


def identity_key(value):
    value = normalized(value)
    groups = [(APPLE_ALIASES, "apple"), *[(aliases, aliases[0]) for aliases, _ in BRANDS]]
    for aliases, canonical in groups:
        for alias in sorted((normalized(item) for item in aliases), key=len, reverse=True):
            if value.startswith(alias):
                return normalized(canonical) + value[len(alias) :]
    return value


def fallback_photos():
    return [
        PhotoRequest(
            target="front",
            reason="确认整体轮廓与正面特征",
            instruction="补拍完整正面，尽量保持清晰。",
        ),
        PhotoRequest(
            target="side",
            reason="区分接口或按键布局",
            instruction="补拍侧面按键或接口，不需要拆机。",
        ),
    ]


def recognize(
    gateway,
    images,
    notes,
    previous,
    subject,
    *,
    search=None,
    fetcher=None,
    on_progress=None,
    registered_domains=None,
):
    on_progress = on_progress or (lambda stage, percent: None)
    gateway.config = gateway.config.model_copy(update={"upstream_max_attempts": 1})
    prior = {**previous.get("previous", {}), "rejectedModels": previous.get("rejectedModels", [])}
    if gateway.config.model_provider == "mock" and "vision" not in gateway.managed_roles:
        observation = Observation(
            category="演示设备",
            features=["本地合成照片，仅验证流程"],
            photoRequests=fallback_photos(),
        )
        references = [
            {
                "id": "mock-ref",
                "url": "https://example.com/demo",
                "title": "演示设备 DEMO-100",
                "text": "演示设备 DEMO-100",
                "sourceKind": "third_party",
            }
        ]
        comparison = Comparison(
            candidates=[
                {
                    "brandName": "演示设备",
                    "modelCode": "DEMO-100",
                    "sourceIds": ["mock-ref"],
                    "matchReasons": ["本地演示候选"],
                }
            ]
        )
        return {
            "candidates": candidates_from_sources(comparison, references, previous, notes),
            "analysis": {
                **previous,
                "observation": observation.model_dump(),
                "photoRequests": [p.model_dump() for p in observation.photoRequests],
                "researchStatus": "found",
            },
            "clarification": "演示候选仅用于本地测试，请由用户选择。",
        }
    owned_search = search is None
    search = search or SearchGateway()
    observation = None
    analysis = dict(previous)
    try:
        with request_scope(time.monotonic() + 140):
            on_progress("observing", 15)
            observation = observe(gateway, images, notes, prior, subject)
            analysis.update(
                observation=observation.model_dump(),
                photoRequests=[
                    p.model_dump() for p in (observation.photoRequests or fallback_photos())
                ],
                researchStatus="searching",
            )
            on_progress("finding_candidates", 35)
            rows, visited = [], set()
            domains = brand_domains(observation.brandHint, registered_domains)
            for query in search_queries(observation, (registered_domains or {}).keys()):
                for row in search.search(
                    query,
                    timeout=8,
                    max_attempts=1,
                    subject=subject,
                    include_domains=domains or None,
                ):
                    if row["url"] not in visited:
                        rows.append(row)
                        visited.add(row["url"])
                if len(rows) >= 5:
                    break
            rows.sort(key=lambda row: not official_url(row["url"], domains))
            references = fetch_references(rows[:5], observation, domains, fetcher=fetcher)
            if not references:
                analysis["researchStatus"] = "no_sources"
                return {
                    "candidates": [],
                    "analysis": analysis,
                    "clarification": (
                        "已完成外观判断，暂未找到足以支持具体型号的资料。"
                        "可以按建议补拍或补充品牌线索。"
                    ),
                }
            on_progress("comparing", 70)
            comparison = compare(
                gateway, images, observation, references, {**prior, "userNotes": notes}, subject
            )
            candidates = candidates_from_sources(
                comparison, references, previous, notes, registered_domains
            )
            analysis.update(
                researchStatus="found" if candidates else "no_match",
                photoRequests=[
                    p.model_dump()
                    for p in (
                        comparison.photoRequests or observation.photoRequests or fallback_photos()
                    )
                ],
            )
            return {
                "candidates": candidates,
                "analysis": analysis,
                "clarification": comparison.clarification
                or (
                    "请比较候选的匹配点和差异，选择型号或继续补拍。"
                    if candidates
                    else "现有资料与照片仍有不符，请按建议补拍或补充线索。"
                ),
            }
    except DomainError as error:
        if observation is None:
            raise
        analysis["researchStatus"] = "limited" if error.status in {429, 504} else "unavailable"
        return {
            "candidates": [],
            "analysis": analysis,
            "clarification": "已保留外观判断和补拍建议，资料比对未完成：" + error.message,
        }
    finally:
        if owned_search:
            search.client.close()
