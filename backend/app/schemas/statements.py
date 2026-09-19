from typing import Any

from pydantic import BaseModel, Field


class StatementAnalysisResponse(BaseModel):
    """Result of analyzing one or more statement images.

    The summary and trend payloads are passed through as-is from the analysis
    scripts rather than re-declared field by field: they are a reporting
    surface that will keep growing, and pinning every key here would mean
    editing this file every time a new figure is computed.
    """

    summary: dict[str, Any] = Field(
        description="Breakdown of the first (or only) statement: totals, categories, merchants."
    )
    summaries: list[dict[str, Any]] = Field(
        description="One summary per uploaded statement, oldest first."
    )
    trend: dict[str, Any] | None = Field(
        default=None,
        description="Cross-month comparison. Null unless at least two statements were sent.",
    )
    narrative: str = Field(description="Short written analysis, grounded in the numbers above.")
