"""텍스트 정규화 및 토크나이저 — CER 계산의 토대.

핵심 원칙
  - NFC 강제: 한글 완성형(NFC)과 조합형(NFD)은 코드포인트 수가 달라, 정규화를
    안 하면 문자 오프셋·CER이 조용히 틀어진다(무성 실패). 모든 입력을 NFC로 못박는다.
  - 음절(NFC 코드포인트) = 헤드라인 CER 토큰.
  - 자모 = 오류 성격 진단용 보조(받침 슬립 vs 완전 다른 단어).
  - 정규화는 공백을 single로 collapse하되 유지한다(N2). 공백 완전제거(N3)는
    '잘 못'(부정) vs '잘못'(과오) 같은 의미전복을 마스킹하므로 도입하지 않는다.
"""

from __future__ import annotations

import unicodedata

# norm CER에서 보존할 의미 있는 기호 (문장부호 category라도 벗기지 않음).
_KEEP_SYMBOLS = set("%‰")


def to_nfc(s: str) -> str:
    """유니코드 NFC(완성형)로 정규화."""
    return unicodedata.normalize("NFC", s)


def syllable_tokens(s: str) -> list[str]:
    """음절(NFC 코드포인트) 단위 토큰 리스트. 공백·문장부호도 각각 한 토큰."""
    return list(to_nfc(s))


def jamo_tokens(s: str) -> list[str]:
    """자모 단위 토큰 리스트.

    표준 정준분해(NFKD)로 한글 음절을 결합 자모로 쪼개고, 호환 자모(단독 'ㅋㅋ'
    'ㅠㅠ')도 결합 자모로 접는다. 산술식(0xAC00) 대신 표준 분해를 써 호환/단독
    자모가 '비한글 단일 토큰'으로 새는 무성 과소집계를 막는다.
    """
    return list(unicodedata.normalize("NFKD", to_nfc(s)))


def normalize_text(s: str) -> str:
    """정규화 후 전체 CER(N2)용 표면 정규화.

    순서: NFC → 라틴 소문자화 → 문장부호 제거 → 공백 collapse(single, 유지).
    숫자 표면형 정규화(3천만=삼천만)는 여기서 하지 않는다 — 그건 엔티티 레벨
    지표(CTER)의 몫이다(자유 텍스트 숫자 경계 탐지는 취약하고, 골든 오프셋으로
    앵커된 엔티티 채점이 훨씬 견고하므로).
    """
    # 문장부호는 제거하되 의미 있는 기호(%, ‰ 등)는 보존한다 — '%'는 category Po라
    # 무조건 벗기면 퍼센트 소실을 norm CER이 '오류 0'으로 흡수한다(S4).
    s = to_nfc(s).lower()
    s = "".join(ch for ch in s if ch in _KEEP_SYMBOLS or not unicodedata.category(ch).startswith("P"))
    # split()은 모든 공백 런을 하나로 접고 양끝을 제거 → N2(공백 유지, N3 아님).
    return " ".join(s.split())
