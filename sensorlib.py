"""
센서 데이터 처리 모듈

GUI(sensor_dashboard.py)와 분리해서, 데이터 읽기와 계산을 담당합니다.

    읽기      : phyphox에서 내보낸 CSV (쉼표·세미콜론·탭 구분, 소수점 쉼표 모두 지원)
    통계      : 평균, 표준편차, 최소, 최대
"""
import csv
import io
import math
import re

import numpy as np


# ============================================================================
# 데이터 구조
# ============================================================================
class Dataset:
    """시간축 하나에 여러 채널이 붙은 데이터. channels: {이름: (값 배열, 단위)}"""

    def __init__(self, title, t, channels):
        self.title = title
        self.t = np.asarray(t, dtype=float)
        self.channels = {k: (np.asarray(v, dtype=float), u) for k, (v, u) in channels.items()}

    def names(self):
        return list(self.channels.keys())

    def __len__(self):
        return len(self.t)


_UNIT_RE = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")


def split_label(label):
    """'Acceleration x (m/s^2)' → ('Acceleration x', 'm/s^2')"""
    label = label.strip().strip('"')
    m = _UNIT_RE.match(label)
    return (m.group(1).strip(), m.group(2).strip()) if m else (label, "")


def _to_float(s, decimal_comma):
    s = s.strip().strip('"')
    if decimal_comma:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return math.nan


def load_csv(path):
    """phyphox CSV 읽기. 첫 열은 시간(s), 나머지 열은 센서 채널로 본다."""
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise ValueError("데이터가 없는 파일이에요.")
    head = lines[0]
    if "\t" in head:
        delim = "\t"
    elif head.count(";") >= 1 and head.count(";") >= head.count(","):
        delim = ";"
    else:
        delim = ","
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))
    header = rows[0]
    if len(header) < 2:
        raise ValueError("열이 2개 이상이어야 해요 (시간 + 센서 값).")
    # 구분자가 ;나 탭이고 값에 쉼표가 있으면 소수점 쉼표로 판단
    decimal_comma = delim != "," and any("," in c for r in rows[1:6] for c in r)
    data = np.array([[_to_float(c, decimal_comma) for c in r[:len(header)]] for r in rows[1:] if len(r) >= len(header)])
    if data.size == 0:
        raise ValueError("숫자 데이터를 읽지 못했어요.")
    data = data[~np.isnan(data[:, 0])]
    order = np.argsort(data[:, 0], kind="stable")
    data = data[order]
    channels = {}
    for j, label in enumerate(header[1:], start=1):
        name, unit = split_label(label)
        base, k = name or f"ch{j}", 2
        while name in channels:
            name, k = f"{base} ({k})", k + 1
        channels[name] = (data[:, j], unit)
    title = path.replace("\\", "/").split("/")[-1]
    return Dataset(title, data[:, 0], channels)


# ============================================================================
# 통계
# ============================================================================
def stats(x):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if not len(x):
        return {"mean": math.nan, "std": math.nan, "min": math.nan, "max": math.nan}
    return {"mean": float(x.mean()), "std": float(x.std(ddof=1)) if len(x) > 1 else 0.0,
            "min": float(x.min()), "max": float(x.max())}
