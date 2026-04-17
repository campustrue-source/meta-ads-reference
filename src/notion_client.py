"""Notion integration for storing ad transcripts.

Handles the 2025-09+ Notion API model where a "database" exposes one or more
"data sources". The ID passed in via NOTION_DATABASE_ID may be either:
  - a data_source id (preferred, works directly), or
  - a legacy database id (auto-resolved to its first data_source).
"""
from __future__ import annotations

import os
from datetime import date
from typing import Any

from notion_client import Client
from notion_client.errors import APIResponseError

NOTION_TEXT_LIMIT = 2000
TRANSCRIPT_PROP = "본문 스크립트"
URL_PROP = "원본 URL"
DATE_PROP = "수집일"
TITLE_PROP_PREFERRED = "릴스 이름"


class NotionError(Exception):
    """Raised when saving to Notion fails."""


def _chunk_text(text: str, limit: int = NOTION_TEXT_LIMIT) -> list[str]:
    if not text:
        return [""]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        split_at = window.rfind("\n")
        if split_at < limit // 2:
            split_at = window.rfind(" ")
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _rich_text_blocks(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": {"content": chunk}} for chunk in _chunk_text(text)]


def _paragraph_blocks(text: str) -> list[dict[str, Any]]:
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": chunk}}]},
        }
        for chunk in _chunk_text(text)
    ]


def _resolve_data_source(client: Client, raw_id: str) -> tuple[str, dict[str, Any]]:
    """Return (data_source_id, data_source_object) for the given id.

    Tries data_sources.retrieve first; if that fails, treats the id as a
    database id and uses its first data_source.
    """
    # Path 1: already a data_source id
    try:
        ds = client.data_sources.retrieve(data_source_id=raw_id)
        return ds["id"], ds
    except APIResponseError:
        pass

    # Path 2: legacy database id — look up its data_sources
    try:
        db = client.databases.retrieve(database_id=raw_id)
    except APIResponseError as exc:
        raise NotionError(
            f"Notion에서 해당 ID를 찾을 수 없습니다. Integration 연결 여부와 ID를 확인하세요. ({exc.code})"
        ) from exc

    sources = db.get("data_sources") or []
    if not sources:
        raise NotionError("DB에 data_source가 없습니다. Notion에서 DB를 점검하세요.")
    ds_id = sources[0]["id"]
    ds = client.data_sources.retrieve(data_source_id=ds_id)
    return ds["id"], ds


def _find_title_property(properties: dict[str, Any]) -> str:
    """Pick the title property: prefer the spec name, else the first title column."""
    if properties.get(TITLE_PROP_PREFERRED, {}).get("type") == "title":
        return TITLE_PROP_PREFERRED
    for name, meta in properties.items():
        if meta.get("type") == "title":
            return name
    raise NotionError("Notion DB에 Title 속성이 없습니다. 하나 만들어주세요.")


def _find_property_by_type(
    properties: dict[str, Any], target_type: str, preferred_names: list[str]
) -> str | None:
    """Pick a property of ``target_type``, preferring ``preferred_names`` then any match."""
    for name in preferred_names:
        if properties.get(name, {}).get("type") == target_type:
            return name
    for name, meta in properties.items():
        if meta.get("type") == target_type:
            return name
    return None


def append_to_database(
    db_id: str,
    name: str,
    transcript: str,
    source_url: str,
) -> str:
    """Create a new Notion page under the given database/data source. Returns URL."""
    if not name.strip():
        raise NotionError("릴스 이름이 비어 있습니다.")
    if not transcript.strip():
        raise NotionError("저장할 스크립트가 비어 있습니다.")

    token = os.getenv("NOTION_TOKEN")
    if not token:
        raise NotionError("NOTION_TOKEN 환경변수가 설정되지 않았습니다.")

    client = Client(auth=token)
    data_source_id, data_source = _resolve_data_source(client, db_id)
    schema: dict[str, dict[str, Any]] = data_source.get("properties") or {}

    title_prop = _find_title_property(schema)

    properties: dict[str, Any] = {
        title_prop: {"title": [{"type": "text", "text": {"content": name.strip()[:2000]}}]},
    }

    transcript_chunks = _chunk_text(transcript)
    use_transcript_property = schema.get(TRANSCRIPT_PROP, {}).get("type") == "rich_text"
    if use_transcript_property:
        properties[TRANSCRIPT_PROP] = {"rich_text": _rich_text_blocks(transcript)}

    if source_url:
        url_prop = _find_property_by_type(
            schema, "url", [URL_PROP, "원본url", "원본 url", "원본URL", "URL", "url"]
        )
        if url_prop:
            properties[url_prop] = {"url": source_url}

    date_prop = _find_property_by_type(schema, "date", [DATE_PROP, "수집일"])
    if date_prop:
        properties[date_prop] = {"date": {"start": date.today().isoformat()}}

    create_kwargs: dict[str, Any] = {
        "parent": {"type": "data_source_id", "data_source_id": data_source_id},
        "properties": properties,
    }
    if not use_transcript_property:
        create_kwargs["children"] = _paragraph_blocks(transcript)

    try:
        page = client.pages.create(**create_kwargs)
    except APIResponseError as exc:
        raise NotionError(f"Notion 페이지 생성 실패: {exc.code} - {exc}") from exc

    url = page.get("url")
    if not url:
        raise NotionError("Notion이 페이지 URL을 반환하지 않았습니다.")

    # If the transcript was very long and we stored it in a property,
    # also append the full text to the page body for readability.
    if use_transcript_property and len(transcript_chunks) > 1:
        try:
            client.blocks.children.append(
                block_id=page["id"],
                children=_paragraph_blocks(transcript),
            )
        except APIResponseError:
            pass

    return url


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    if len(sys.argv) < 3:
        print("usage: python -m src.notion_client <name> <transcript>")
        sys.exit(1)
    db_id = os.getenv("NOTION_DATABASE_ID")
    if not db_id:
        print("NOTION_DATABASE_ID is not set")
        sys.exit(1)
    page_url = append_to_database(db_id, sys.argv[1], sys.argv[2], "")
    print(f"created: {page_url}")
