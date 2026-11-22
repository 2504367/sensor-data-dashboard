"""
센서 데이터 처리 모듈

GUI(sensor_dashboard.py)와 분리해서, 데이터 읽기·처리·내보내기·실시간 수집을 담당합니다.

    읽기      : phyphox에서 내보낸 CSV (쉼표·세미콜론·탭 구분, 소수점 쉼표 모두 지원)
    처리      : 이상치 제거(Hampel 필터), 이동평균, 통계, FFT 주 주파수, 기압 → 고도 변환
    내보내기  : XLSX(openpyxl) 또는 CSV
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
# 처리
# ============================================================================
def hampel(x, window=7, n_sigma=3.0):
    """Hampel 필터: 주변 window개 값의 중앙값에서 MAD 기준 n_sigma배 넘게 벗어난 점을 이상치로 보고 중앙값으로 바꾼다.
    반환: (정리된 배열, 이상치 위치 bool 배열)"""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 3 or window < 3:
        return x.copy(), np.zeros(n, dtype=bool)
    h = window // 2
    pad = np.pad(x, h, mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(pad, 2 * h + 1)
    med = np.nanmedian(win, axis=1)
    mad = 1.4826 * np.nanmedian(np.abs(win - med[:, None]), axis=1)
    # 신호 전체의 노이즈 크기(인접한 값 차이의 MAD / √2)를 하한으로 둔다.
    # 이게 없으면 매끄러운 곡선의 꼭짓점처럼 주변 값이 고른 곳에서 정상 값도 이상치로 잘못 잡힌다.
    d = np.diff(x[~np.isnan(x)])
    noise = 1.4826 * np.median(np.abs(d - np.median(d))) / np.sqrt(2) if len(d) > 2 else 0.0
    out = np.abs(x - med) > n_sigma * np.maximum(np.maximum(mad, noise), 1e-12)
    out &= ~np.isnan(x)
    y = x.copy()
    y[out] = med[out]
    return y, out


def moving_average(x, window):
    x = np.asarray(x, dtype=float)
    if window <= 1 or len(x) < 2:
        return x.copy()
    window = min(window, len(x))
    h = window // 2
    pad = np.pad(x, (h, window - 1 - h), mode="edge")
    return np.convolve(pad, np.ones(window) / window, mode="valid")


def stats(x):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if not len(x):
        return {"mean": math.nan, "std": math.nan, "min": math.nan, "max": math.nan}
    return {"mean": float(x.mean()), "std": float(x.std(ddof=1)) if len(x) > 1 else 0.0,
            "min": float(x.min()), "max": float(x.max())}


def dominant_frequency(t, x, fmin=0.05, min_ratio=8.0):
    """균일한 시간 간격으로 다시 샘플링한 뒤 FFT로 가장 센 주파수(Hz)를 찾는다. 평균(DC)은 빼고 계산."""
    t, x = np.asarray(t, float), np.asarray(x, float)
    ok = ~np.isnan(x)
    t, x = t[ok], x[ok]
    if len(t) < 16 or t[-1] - t[0] <= 0:
        return math.nan
    n = len(t)
    tu = np.linspace(t[0], t[-1], n)
    xu = np.interp(tu, t, x)
    xu = xu - np.polyval(np.polyfit(tu - tu[0], xu, 1), tu - tu[0])   # 평균과 직선 추세 제거
    xu = xu * np.hanning(n)
    spec = np.abs(np.fft.rfft(xu))
    freqs = np.fft.rfftfreq(n, d=(tu[-1] - tu[0]) / (n - 1))
    band = freqs >= fmin
    if not band.any() or spec[band].max() <= 0:
        return math.nan
    k = np.argmax(np.where(band, spec, 0))
    # 뚜렷한 봉우리가 없으면(노이즈뿐이면) 주파수가 의미 없으므로 표시하지 않음
    if spec[k] < min_ratio * np.median(spec[band]):
        return math.nan
    # 이웃 3점에 포물선을 맞춰 주파수 해상도보다 정밀하게 보정
    if 0 < k < len(spec) - 1:
        a, b, c = spec[k - 1], spec[k], spec[k + 1]
        d = (a - c) / (2 * (a - 2 * b + c)) if (a - 2 * b + c) != 0 else 0
        return float(freqs[k] + d * (freqs[1] - freqs[0]))
    return float(freqs[k])


def pressure_to_altitude(p_hpa, p0_hpa=None):
    """국제표준대기 기압 공식: h = 44330 · (1 − (p/p0)^0.1903). p0를 안 주면 첫 값을 기준(0 m)으로."""
    p = np.asarray(p_hpa, dtype=float)
    if p0_hpa is None:
        first = p[~np.isnan(p)]
        p0_hpa = first[0] if len(first) else 1013.25
    return 44330.0 * (1.0 - np.power(p / p0_hpa, 0.1903))


def is_pressure(name, unit):
    return "pressure" in name.lower() or unit.lower() in ("hpa", "mbar")


def process(ds, ch, outlier=True, hampel_window=7, smooth=1, altitude=False):
    """채널 하나 처리 → dict(raw, clean, outliers, smooth, label, unit)"""
    raw, unit = ds.channels[ch]
    clean, mask = hampel(raw, hampel_window) if outlier else (raw.copy(), np.zeros(len(raw), bool))
    sm = moving_average(clean, smooth)
    label = ch
    if altitude and is_pressure(ch, unit):
        sm, clean, raw = pressure_to_altitude(sm), pressure_to_altitude(clean), pressure_to_altitude(raw)
        label, unit = "Relative altitude", "m"
    return {"raw": raw, "clean": clean, "outliers": mask, "smooth": sm, "label": label, "unit": unit}


# ============================================================================
# 내보내기
# ============================================================================
def export(path, ds, processed, summary_rows):
    """processed: {채널: process() 결과}, summary_rows: [[채널, 단위, 평균, ...], ...]"""
    header = ["Time (s)"]
    cols = [ds.t]
    for ch, p in processed.items():
        u = f" ({p['unit']})" if p["unit"] else ""
        header += [f"{p['label']} raw{u}", f"{p['label']} filtered{u}", f"{p['label']} outlier"]
        cols += [p["raw"], p["smooth"], p["outliers"].astype(int)]
    sum_header = ["Channel", "Unit", "Mean", "Std", "Min", "Max", "Dominant freq (Hz)", "Outliers removed"]
    if path.lower().endswith(".xlsx"):
        from openpyxl import Workbook
        from openpyxl.styles import Font
        wb = Workbook()
        ws = wb.active
        ws.title = "data"
        ws.append(header)
        for c in ws[1]:
            c.font = Font(bold=True)
        for i in range(len(ds.t)):
            ws.append([_cell(col[i]) for col in cols])
        ws.freeze_panes = "B2"
        ws2 = wb.create_sheet("summary")
        ws2.append(sum_header)
        for c in ws2[1]:
            c.font = Font(bold=True)
        for r in summary_rows:
            ws2.append([_cell(v) for v in r])
        for sh in (ws, ws2):
            for col in sh.columns:
                sh.column_dimensions[col[0].column_letter].width = max(12, min(32, len(str(col[0].value)) + 2))
        wb.save(path)
    else:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(header)
            for i in range(len(ds.t)):
                w.writerow([_cell(col[i]) for col in cols])


def _cell(v):
    if isinstance(v, (float, np.floating)):
        return None if math.isnan(v) else float(v)
    if isinstance(v, np.integer):
        return int(v)
    return v
