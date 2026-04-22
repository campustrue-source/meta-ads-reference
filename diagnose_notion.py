"""Notion 연동 진단 스크립트. 어디서 끊기는지 찾아준다."""
from __future__ import annotations

import os
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from dotenv import load_dotenv
from notion_client import Client
from notion_client.errors import APIResponseError

load_dotenv()

token = os.getenv("NOTION_TOKEN")
db_id = os.getenv("NOTION_DATABASE_ID")

print("=" * 60)
print("NOTION 진단")
print("=" * 60)

if not token:
    sys.exit("❌ NOTION_TOKEN 이 .env 에 없습니다.")
if not db_id:
    sys.exit("❌ NOTION_DATABASE_ID 가 .env 에 없습니다.")

print(f"✓ NOTION_TOKEN 로드됨 (prefix: {token[:10]}..., len={len(token)})")
print(f"✓ NOTION_DATABASE_ID 로드됨: {db_id}")
print()

client = Client(auth=token)

# 1. 토큰 유효성: 사용자 정보 조회
print("[1/4] 토큰 유효성 확인 (users.me)...")
try:
    me = client.users.me()
    print(f"  ✅ 토큰 OK. Integration 이름: {me.get('name')}")
    print(f"     type={me.get('type')}, id={me.get('id')}")
except APIResponseError as e:
    print(f"  ❌ 토큰 오류: {e.code} — {e}")
    print("     → Notion 에서 Integration 이 아직 살아있는지, 토큰을 다시 발급받아야 하는지 확인")
    sys.exit(1)
print()

# 2. data_source 로 retrieve 시도
print("[2/4] data_source 로 조회 시도...")
ds_ok = False
try:
    ds = client.data_sources.retrieve(data_source_id=db_id)
    print(f"  ✅ data_source 조회 성공. id={ds['id']}")
    print(f"     properties: {list((ds.get('properties') or {}).keys())}")
    ds_ok = True
except APIResponseError as e:
    print(f"  ⚠  data_source 조회 실패: {e.code} — {e}")
    print("     → legacy database id 일 수 있음. 다음 단계로 넘어감.")
print()

# 3. database 로 retrieve 시도
if not ds_ok:
    print("[3/4] database 로 조회 시도...")
    try:
        db = client.databases.retrieve(database_id=db_id)
        print(f"  ✅ database 조회 성공. title={[t['plain_text'] for t in db.get('title', [])]}")
        sources = db.get("data_sources") or []
        print(f"     data_sources 개수: {len(sources)}")
        if sources:
            first = sources[0]["id"]
            print(f"     첫 data_source id: {first}")
            try:
                ds = client.data_sources.retrieve(data_source_id=first)
                print(f"     ✅ 첫 data_source retrieve 성공")
                print(f"     properties: {list((ds.get('properties') or {}).keys())}")
            except APIResponseError as e:
                print(f"     ❌ 첫 data_source retrieve 실패: {e.code} — {e}")
                sys.exit(1)
        else:
            print("  ❌ data_sources 가 비어있음. DB를 Notion에서 점검 필요.")
            sys.exit(1)
    except APIResponseError as e:
        print(f"  ❌ database 조회도 실패: {e.code} — {e}")
        if e.code == "object_not_found":
            print("     → 원인 후보:")
            print("       1) Integration 이 해당 DB 에 Connect 되어 있지 않음 (가장 흔함)")
            print("          Notion → DB 우상단 ··· → Connections → Integration 추가")
            print("       2) NOTION_DATABASE_ID 오타 / 다른 DB id")
            print("       3) DB 가 삭제됨 / 휴지통으로 이동됨")
        elif e.code == "unauthorized":
            print("     → 토큰이 이 DB 에 접근 권한이 없음. Integration 을 DB 에 Connect.")
        sys.exit(1)
else:
    print("[3/4] database 조회 스킵 (data_source 로 이미 성공)")
print()

# 4. 속성 체크
print("[4/4] 필수 속성 체크...")
props = ds.get("properties") or {}
required = {
    "릴스 이름": "title",
    "본문 스크립트": "rich_text",
    "원본 URL": "url",
    "수집일": "date",
}
for name, expected_type in required.items():
    if name in props:
        actual = props[name].get("type")
        icon = "✅" if actual == expected_type else "⚠ "
        print(f"  {icon} '{name}': {actual} (기대: {expected_type})")
    else:
        print(f"  ❌ '{name}' 속성이 없음 (기대 타입: {expected_type})")

print()
print("=" * 60)
print("진단 완료. 위 결과를 보고 어느 단계에서 막혔는지 확인하세요.")
print("=" * 60)
