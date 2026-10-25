from pydantic import BaseModel, Field, HttpUrl


class ScanRequest(BaseModel):
    url: HttpUrl


class StatusRequest(BaseModel):
    status: str = Field(pattern="^(new|reviewed|qualified|disqualified)$")


class DiscoverRequest(BaseModel):
    industry: str = Field(min_length=2, max_length=100)
    location: str = Field(default="", max_length=100)
    keywords: str = Field(default="", max_length=100)
    country: str = Field(default="IN", pattern="^[A-Z]{2}$")
    limit: int = Field(default=10, ge=1, le=20)
