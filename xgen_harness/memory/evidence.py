"""Evidence Workspace — 검색 에이전트의 외부화 작업기억 (state-externalizing harness).

## 배경 (하네스 엔지니어링 철학)
검색 에이전트를 "커지는 transcript 위의 policy" 로 학습/실행하면, 모델이 *의미 결정*
(무엇을 검색·보존·검증·중단할지) 과 *기계적 bookkeeping* (무엇을 봤는지, 어떤 근거가
유효한지, 어떤 주장이 검증됐는지) 을 동시에 떠안는다. Harness-1 (arXiv 2606.02373) 의
핵심 명제: bookkeeping 은 환경(하네스)이 더 안정적으로 들고, policy 는 의미 결정만 한다.

이 모듈은 그 "환경 측 작업기억" 을 엔진 1급 자료구조로 제공한다 — **도메인 agnostic**:
- policy(LLM) 는 `curate` / `verify` / `discard` 로 *의미 결정* 만 emit.
- harness 는 중요도 랭킹 · 중복제거 · cap · 검증 기록 · step-out 렌더를 *소유*.

## Progressive Disclosure 정합
EvidenceSet 은 PD([[store]] 의 pd_stores)의 확장이다. 전체 본문은 `pd_stores["evidence"]`
로 보존돼 `fetch_pd("evidence", id)` 로 **step-in**, compact 뷰는 `render()` 로 **step-out**.
세션 압축(s06)이 messages 를 잘라내도 evidence 는 state 측에 살아남아 보존된다.

## 무하드코딩
cap / 렌더 예산은 전부 파라미터. 매직넘버를 로직에 박지 않는다(기본값만 둔다).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .dedupe import content_fingerprint

# 기본 cap — Harness-1 의 curated set(≤30) 정신. 데이터타입 기본값일 뿐 로직 상수 아님.
DEFAULT_EVIDENCE_CAP = 50


class Importance(str, Enum):
    """근거 중요도 태그. JSON 직렬화를 위해 str 혼합 Enum."""
    VERY_HIGH = "very_high"
    HIGH = "high"
    FAIR = "fair"
    LOW = "low"

    @property
    def rank(self) -> int:
        """정렬/eviction 용 순위 (높을수록 중요)."""
        return _IMPORTANCE_RANK[self]

    @classmethod
    def coerce(cls, value: Any) -> "Importance":
        """문자열/Enum/None → Importance. 알 수 없으면 FAIR."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except (ValueError, AttributeError):
            return cls.FAIR


_IMPORTANCE_RANK = {
    Importance.VERY_HIGH: 3,
    Importance.HIGH: 2,
    Importance.FAIR: 1,
    Importance.LOW: 0,
}


@dataclass
class EvidenceItem:
    """단일 근거 항목 — curated set 의 한 줄.

    id          — 안정 식별자 (LLM 이 부여하거나 source/chunk id).
    content     — 근거 본문(또는 snippet). 전체 본문은 pd_stores 로 step-in 보존.
    source      — 출처(파일/URL/컬렉션 등) — citation 을 state 로 1급화.
    importance  — Importance 태그. harness 가 cap eviction·렌더 순서에 사용.
    score       — 검색 점수 등 보조 정렬 신호.
    verified    — None=미검증 / True/False = verify 결과 (verification 기록).
    verdict     — 검증 메모(왜 pass/fail).
    turn        — curate 된 loop iteration (recency).
    fingerprint — 내용 지문 (dedup 키).
    """
    id: str
    content: str = ""
    source: str = ""
    importance: Importance = Importance.FAIR
    score: float = 0.0
    verified: Optional[bool] = None
    verdict: str = ""
    turn: int = 0
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "source": self.source,
            "importance": self.importance.value,
            "score": self.score,
            "verified": self.verified,
            "verdict": self.verdict,
            "turn": self.turn,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceItem":
        return cls(
            id=str(data["id"]),
            content=str(data.get("content", "")),
            source=str(data.get("source", "")),
            importance=Importance.coerce(data.get("importance", "fair")),
            score=float(data.get("score", 0.0) or 0.0),
            verified=data.get("verified", None),
            verdict=str(data.get("verdict", "")),
            turn=int(data.get("turn", 0) or 0),
            fingerprint=str(data.get("fingerprint", "")),
        )


@dataclass
class EvidenceSet:
    """근거 워크스페이스 — 중요도 태깅 + dedup + cap 된 보존 집합.

    policy 는 `curate`/`verify`/`discard` 로 의미 결정만, harness(이 객체)가 dedup·cap·
    랭킹·렌더를 소유한다. JSON 직렬화 가능 → SessionStore([[store]]) 로 세션 간 인계.
    """
    cap: int = DEFAULT_EVIDENCE_CAP
    items: list[EvidenceItem] = field(default_factory=list)

    # ── 조회 ──
    def get(self, item_id: str) -> Optional[EvidenceItem]:
        return next((i for i in self.items if i.id == item_id), None)

    def _find_by_fingerprint(self, fp: str) -> Optional[EvidenceItem]:
        if not fp:
            return None
        return next((i for i in self.items if i.fingerprint == fp), None)

    def ranked(self) -> list[EvidenceItem]:
        """중요도 → score → recency 내림차순. 렌더/반환의 표준 순서."""
        return sorted(
            self.items,
            key=lambda i: (i.importance.rank, i.score, i.turn),
            reverse=True,
        )

    # ── 변경 (policy 의 의미 결정) ──
    def curate(
        self,
        *,
        id: str,
        content: str,
        source: str = "",
        importance: Any = Importance.FAIR,
        score: float = 0.0,
        turn: int = 0,
    ) -> EvidenceItem:
        """근거를 집합에 promote. 동일 id 또는 동일 내용지문이면 갱신(중복 누적 방지).

        중요도는 상향만(downgrade 금지) — 한 번 중요하다고 본 근거를 약화시키지 않음.
        cap 초과 시 최저 중요도+오래된 것부터 eviction.
        """
        imp = Importance.coerce(importance)
        fp = content_fingerprint(content)
        existing = self.get(id) or self._find_by_fingerprint(fp)
        if existing is not None:
            if content:
                existing.content = content
            if source:
                existing.source = source
            if imp.rank > existing.importance.rank:
                existing.importance = imp
            if score:
                existing.score = score
            if turn:
                existing.turn = turn
            existing.fingerprint = fp
            return existing

        item = EvidenceItem(
            id=id, content=content, source=source,
            importance=imp, score=score, turn=turn, fingerprint=fp,
        )
        self.items.append(item)
        self._enforce_cap()
        return item

    def verify(self, item_id: str, ok: bool, note: str = "") -> Optional[EvidenceItem]:
        """근거의 검증 결과 기록 (verification cache). 없으면 None."""
        it = self.get(item_id)
        if it is None:
            return None
        it.verified = bool(ok)
        if note:
            it.verdict = note
        return it

    def discard(self, item_id: str) -> bool:
        """근거 제거. 제거했으면 True."""
        before = len(self.items)
        self.items = [i for i in self.items if i.id != item_id]
        return len(self.items) != before

    def _enforce_cap(self) -> None:
        """cap>0 이고 초과면 최저 중요도 → 오래된 → 저점수 순으로 eviction."""
        if self.cap > 0 and len(self.items) > self.cap:
            self.items.sort(key=lambda i: (i.importance.rank, i.turn, i.score))
            overflow = len(self.items) - self.cap
            del self.items[:overflow]

    # ── step-out 렌더 ──
    def render(self, *, max_items: int = 0, max_chars: int = 0, header: bool = True) -> str:
        """compact 뷰 (step-out). 중요도 순. max_items/max_chars 예산 존중(0=무제한).

        본문 전체가 아니라 한 줄 요약만 — 전체는 fetch_pd("evidence", id) 로 step-in.
        """
        ordered = self.ranked()
        if max_items > 0:
            ordered = ordered[:max_items]
        if not ordered:
            return "[evidence workspace: empty]"

        lines: list[str] = []
        if header:
            lines.append(f"[evidence workspace: {len(self.items)} item(s), cap={self.cap}]")
        used = len(lines[0]) if lines else 0
        for it in ordered:
            mark = "✓" if it.verified is True else ("✗" if it.verified is False else "·")
            snippet = " ".join((it.content or "").split())
            if len(snippet) > 160:
                snippet = snippet[:160] + "…"
            src = f" ⟨{it.source}⟩" if it.source else ""
            line = f"- ({it.importance.value} {mark}) {it.id}: {snippet}{src}"
            if max_chars > 0 and used + len(line) + 1 > max_chars:
                lines.append(f"… (+{len(ordered) - len(lines) + (1 if header else 0)} more, budget reached)")
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

    # ── 직렬화 (세션 인계) ──
    def to_dict(self) -> dict[str, Any]:
        return {"cap": self.cap, "items": [i.to_dict() for i in self.items]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceSet":
        data = data or {}
        return cls(
            cap=int(data.get("cap", DEFAULT_EVIDENCE_CAP) or DEFAULT_EVIDENCE_CAP),
            items=[EvidenceItem.from_dict(d) for d in data.get("items", [])],
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "EvidenceSet":
        return cls.from_dict(json.loads(text))
