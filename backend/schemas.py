"""Pydantic v2 schemas for Content Detective API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class PostCreate(BaseModel):
    brand: str
    platform: str
    post_id_ext: str
    url: str | None = None
    caption: str | None = None
    hashtags: str | None = None
    post_date: str | None = None
    weekday_pkt: str | None = None
    hour_pkt: int | None = None
    media_type: str | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    views: int | None = None
    followers_on_page: int | None = None
    engagement_rate: float | None = None
    source: str | None = None
    collector: str | None = None
    collected_at: str | None = None


class PostResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    brand: str
    platform: str
    post_id_ext: str
    url: str | None = None
    caption: str | None = None
    hashtags: str | None = None
    post_date: str | None = None
    weekday_pkt: str | None = None
    hour_pkt: int | None = None
    media_type: str | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    views: int | None = None
    followers_on_page: int | None = None
    engagement_rate: float | None = None
    source: str | None = None
    collector: str | None = None
    collected_at: str | None = None


class BrandResponse(BaseModel):
    name: str
    instagram_handle: str | None = None
    youtube_channel_id: str | None = None


class GraphDataset(BaseModel):
    label: str
    data: list[float | None]
    backgroundColor: list[str] | str | None = None
    borderColor: list[str] | str | None = None


class GraphData(BaseModel):
    labels: list[str]
    datasets: list[GraphDataset]
    meta: dict[str, Any] = {}


class ChatRequest(BaseModel):
    question: str | None = None
    message: str | None = None
    history: list[Any] = []
    mode: str = "chat"
    platform: str | None = None
    brand: str | None = None


class ChatResponse(BaseModel):
    intent: str = ""
    text: str = ""
    blocks: list[Any] = []
    notes: list[Any] = []
    evidence: str = ""
    follow_ups: list[str] = []
