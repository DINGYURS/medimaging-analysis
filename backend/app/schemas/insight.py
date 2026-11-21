"""Structured, non-diagnostic AI insight contract."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictStr

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

class Interpretation(StrictModel):
    lesion_id: StrictStr
    text: StrictStr = Field(min_length=1, max_length=1200)
    evidence_ids: list[StrictStr] = Field(max_length=20)

class ReviewItem(StrictModel):
    action: StrictStr = Field(min_length=1, max_length=600)
    reason: StrictStr = Field(min_length=1, max_length=600)
    evidence_ids: list[StrictStr] = Field(max_length=20)

class InsightOutput(StrictModel):
    schema_version: Literal["clinical-insight-v2"]
    analysis_status: Literal["ready", "limited", "insufficient"]
    result_summary: StrictStr = Field(min_length=1, max_length=2400)
    quantitative_interpretation: list[Interpretation] = Field(max_length=100)
    review_items: list[ReviewItem] = Field(max_length=12)
    limitations: list[StrictStr] = Field(max_length=20)
