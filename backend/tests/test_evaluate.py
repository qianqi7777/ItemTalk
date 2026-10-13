"""评测工具使用受控 fixture 验证指标计算，不宣称真实检索质量。"""

from uuid import UUID

import pytest

from app.db import one, transaction
from app.evaluate import GoldCase, evaluate
from app.worker import run_once
from tests.conftest import register_login
from tests.test_api import identified_product

pytestmark = pytest.mark.postgres


def test_gold_set_reports_expected_evidence_page_and_region(client):
    register_login(client)
    product = identified_product(client)
    assert run_once()
    with transaction() as connection:
        version = one(connection, "SELECT id FROM document_versions")["id"]
    case = GoldCase(
        productModelId=UUID(product["productModelId"]),
        question="如何开机",
        expectedDocumentVersionIds=[version],
        expectedPage=1,
        expectedRegion="CN",
    )
    report = evaluate([case])
    assert report == {
        "cases": 1,
        "recallAt10": 1,
        "pageAccuracy": 1,
        "applicabilityAccuracy": 1,
        "noEvidenceAccuracy": None,
    }
