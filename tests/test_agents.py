import pytest
from pydantic import ValidationError

from growthcrew.schemas import Claim


def test_claim_requires_http_source_url():
    with pytest.raises(ValidationError):
        Claim(statement="Competitor X is cheaper", source_url="trust me")
