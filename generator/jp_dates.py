"""日付の和暦変換・全角化ユーティリティ（純Python）。"""
from __future__ import annotations

# 元号境界（開始日 → 元号名・元年西暦）
ERAS = [
    (19890108, "平成", 1989),
    (20190501, "令和", 2019),
]
# 平成より前は本プロジェクトでは扱わない想定だが念のため保険を入れる
EARLY_ERAS = [
    (19260101, "昭和", 1926),
]

ZEN = "０１２３４５６７８９"


def _to_zen(s: str) -> str:
    return s.translate(str.maketrans("0123456789", ZEN))


def _to_zen_int(n: int) -> str:
    return _to_zen(str(n))


def from_yyyymmdd(s: str) -> tuple[int, int, int]:
    """YYYYMMDD 文字列を (year, month, day) int タプルに。"""
    if not s or len(s) != 8 or not s.isdigit():
        raise ValueError(f"invalid YYYYMMDD: {s!r}")
    return int(s[:4]), int(s[4:6]), int(s[6:8])


def to_wareki(yyyymmdd: str) -> str:
    """YYYYMMDD → 「令和●年●月●日」（全角・元年は『元年』）。"""
    y, m, d = from_yyyymmdd(yyyymmdd)
    yyyymmdd_int = int(yyyymmdd)
    name, base = "令和", 2019
    for boundary, n, b in ERAS:
        if yyyymmdd_int >= boundary:
            name, base = n, b
    if yyyymmdd_int < ERAS[0][0]:
        for boundary, n, b in EARLY_ERAS:
            if yyyymmdd_int >= boundary:
                name, base = n, b
    nen = y - base + 1
    nen_str = "元" if nen == 1 else _to_zen_int(nen)
    return f"{name}{nen_str}年{_to_zen_int(m)}月{_to_zen_int(d)}日"


def to_seireki_wareki(yyyymmdd: str) -> str:
    """YYYYMMDD → 「２０●●年（令和●年）●月●日」（PCT国際出願日等）。"""
    y, m, d = from_yyyymmdd(yyyymmdd)
    yyyymmdd_int = int(yyyymmdd)
    name, base = "令和", 2019
    for boundary, n, b in ERAS:
        if yyyymmdd_int >= boundary:
            name, base = n, b
    nen = y - base + 1
    nen_str = "元" if nen == 1 else _to_zen_int(nen)
    return f"{_to_zen_int(y)}年（{name}{nen_str}年）{_to_zen_int(m)}月{_to_zen_int(d)}日"


def to_seireki(yyyymmdd: str) -> str:
    """YYYYMMDD → 「２０●●年●月●日」 形式（純西暦）。"""
    y, m, d = from_yyyymmdd(yyyymmdd)
    return f"{_to_zen_int(y)}年{_to_zen_int(m)}月{_to_zen_int(d)}日"


# 国コード → 国名表記（短縮形優先）
COUNTRY_CD_MAP = {
    "KR": "韓国",
    "US": "米国",
    "CN": "中国",
    "JP": "日本",
    "EP": "欧州特許庁",
    "DE": "ドイツ",
    "GB": "英国",
    "FR": "フランス",
    "TW": "台湾",  # ※台湾は「パリ条約の例による優先権主張」表記なので別扱いも要
}


def country_name(cd: str) -> str:
    return COUNTRY_CD_MAP.get(cd, cd or "")
