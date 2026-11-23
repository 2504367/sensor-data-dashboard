"""
센서 데이터 처리 모듈

GUI(sensor_dashboard.py)와 분리해서, 데이터 읽기·처리·내보내기·실시간 수집을 담당합니다.

    읽기      : phyphox에서 내보낸 CSV (쉼표·세미콜론·탭 구분, 소수점 쉼표 모두 지원)
    처리      : 이상치 제거(Hampel 필터), 이동평균, 통계, FFT 주 주파수, 기압 → 고도 변환
    내보내기  : XLSX(openpyxl) 또는 CSV
    실시간    : phyphox 원격 접속(REST API) 또는 시뮬레이션 데이터를 별도 스레드에서 수집
"""
import csv
import io
import json
import math
import re
import threading
import time
import urllib.request

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


# ============================================================================
# 실시간 수집
# ============================================================================
class LiveBuffer:
    """수집 스레드가 쓰고 GUI가 읽는 공유 버퍼. 잠금(lock)으로 동시에 접근해도 데이터가 꼬이지 않게 한다."""

    def __init__(self, max_points=200_000):
        self.lock = threading.Lock()
        self.max_points = max_points
        self.clear()

    def clear(self):
        with getattr(self, "lock", threading.Lock()):
            self.t = []
            self.cols = {}
            self.units = {}

    def setup(self, units):
        with self.lock:
            self.t, self.cols, self.units = [], {k: [] for k in units}, dict(units)

    def extend(self, t, values):
        """t: 시간 리스트, values: {채널: 값 리스트} (길이가 t와 같아야 함)"""
        with self.lock:
            self.t.extend(t)
            for k, v in values.items():
                self.cols[k].extend(v)
            extra = len(self.t) - self.max_points
            if extra > 0:
                del self.t[:extra]
                for k in self.cols:
                    del self.cols[k][:extra]

    def snapshot(self, title="live", since=None):
        with self.lock:
            t = np.array(self.t)
            ch = {k: (np.array(v), self.units[k]) for k, v in self.cols.items()}
        if since is not None and len(t):
            m = t >= since
            t = t[m]
            ch = {k: (v[m], u) for k, (v, u) in ch.items()}
        return Dataset(title, t, ch)


class SimulatedSource(threading.Thread):
    """phyphox 없이 시험할 수 있는 가짜 센서. 진자처럼 2 Hz로 흔들리는 가속도 + 가끔 튀는 값,
    천천히 바뀌는 조도, 계단을 오르듯 내려가는 기압을 50 Hz로 만든다."""

    def __init__(self, buf, rate=50.0):
        super().__init__(daemon=True)
        self.buf, self.rate = buf, rate
        self.running = threading.Event()
        self.error = None
        self.rng = np.random.default_rng()

    def run(self):
        units = {"Acceleration x": "m/s^2", "Acceleration y": "m/s^2", "Acceleration z": "m/s^2",
                 "Illuminance": "lx", "Pressure": "hPa"}
        self.buf.setup(units)
        self.running.set()
        t0, n = time.time(), 0
        while self.running.is_set():
            now = time.time() - t0
            ts, vals = [], {k: [] for k in units}
            while n / self.rate <= now:
                t = n / self.rate
                ax = 1.5 * math.sin(2 * math.pi * 2.0 * t) * math.exp(-t / 40) + self.rng.normal(0, 0.15)
                if self.rng.random() < 0.01:
                    ax += self.rng.choice([-1, 1]) * self.rng.uniform(4, 8)   # 튀는 값 (이상치)
                ts.append(t)
                vals["Acceleration x"].append(ax)
                vals["Acceleration y"].append(0.3 * math.sin(2 * math.pi * 2.0 * t + 1) + self.rng.normal(0, 0.1))
                vals["Acceleration z"].append(9.81 + self.rng.normal(0, 0.1))
                vals["Illuminance"].append(300 + 150 * math.sin(2 * math.pi * 0.05 * t) + self.rng.normal(0, 5))
                vals["Pressure"].append(1013.0 - 0.012 * min(t, 30) * 3 + self.rng.normal(0, 0.008))  # 약 3 m 상승
                n += 1
            if ts:
                self.buf.extend(ts, vals)
            time.sleep(0.05)

    def stop(self):
        self.running.clear()


def guess_unit(buffer_name):
    """phyphox 버퍼 이름으로 단위 추정 (실시간 연결은 단위 정보를 주지 않음)"""
    n = buffer_name.lower()
    for key, unit in (("acc", "m/s^2"), ("gyr", "rad/s"), ("mag", "µT"), ("illum", "lx"), ("light", "lx"),
                      ("press", "hPa"), ("prox", "cm"), ("temp", "°C")):
        if key in n:
            return unit
    return ""


class PhyphoxSource(threading.Thread):
    """phyphox 앱의 '원격 접속(Remote access)'으로 데이터를 받아온다.

    /config 로 버퍼 이름을 알아내고, /get?시간버퍼=마지막시각&값버퍼=마지막시각|시간버퍼 형식으로
    새로 쌓인 값만 반복해서 가져온다 (phyphox 원격 인터페이스 문서의 threshold 방식)."""

    def __init__(self, buf, address, interval=0.2, start_measuring=True):
        super().__init__(daemon=True)
        address = address.strip()
        if not address.startswith("http"):
            address = "http://" + address
        self.base = address.rstrip("/")
        self.buf, self.interval, self.start_measuring = buf, interval, start_measuring
        self.running = threading.Event()
        self.error = None
        self.groups = {}      # 시간 버퍼 → [값 버퍼들]
        self.session = None

    def _get(self, path, timeout=3.0):
        with urllib.request.urlopen(self.base + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    @staticmethod
    def group_buffers(names):
        """버퍼 이름 목록을 시간 버퍼별로 묶는다. 예: accX, accY, accZ, acc_time → {acc_time: [accX, accY, accZ]}"""
        times = [n for n in names if n.lower().endswith("time") or n.lower() in ("t",)]
        groups = {}
        for n in names:
            if n in times:
                continue
            stem = re.sub(r"[_]?[xyzXYZ]$", "", n)
            match = next((tb for tb in times if tb.lower().startswith(stem.lower())), None)
            if match is None and len(times) == 1:
                match = times[0]
            if match:
                groups.setdefault(match, []).append(n)
        return groups

    def connect(self):
        cfg = self._get("/config")
        names = [b["name"] for b in cfg.get("buffers", [])]
        self.groups = self.group_buffers(names)
        if not self.groups:
            raise RuntimeError(f"시간 버퍼를 찾지 못했어요. 버퍼 목록: {', '.join(names)}")
        return cfg.get("localTitle") or cfg.get("title") or "phyphox"

    def run(self):
        try:
            title = self.connect()
            self.title = title
            # 시간 버퍼가 여러 개면 첫 번째 그룹만 사용 (센서마다 측정 시각이 달라서 한 축에 못 합침)
            tb = next(iter(self.groups))
            vals = self.groups[tb]
            self.buf.setup({v: guess_unit(v) for v in vals})
            if self.start_measuring:
                self._get("/control?cmd=start")
            self.running.set()
            last = None
            while self.running.is_set():
                if last is None:
                    q = "&".join([f"{tb}=full"] + [f"{v}=full" for v in vals])
                else:
                    q = "&".join([f"{tb}={last!r}"] + [f"{v}={last!r}|{tb}" for v in vals])
                d = self._get("/get?" + q)
                sess = d.get("status", {}).get("session")
                if self.session is not None and sess != self.session:   # 앱에서 데이터를 지우면 세션이 바뀜
                    self.buf.setup({v: guess_unit(v) for v in vals})
                    last = None
                self.session = sess
                tt = d["buffer"][tb]["buffer"]
                cols = {v: d["buffer"][v]["buffer"] for v in vals}
                m = min([len(tt)] + [len(c) for c in cols.values()])
                if last is not None and m:          # 경계값이 한 번 더 오는 경우를 대비해 중복 제거
                    k = 0
                    while k < m and tt[k] is not None and tt[k] <= last:
                        k += 1
                    tt, cols, m = tt[k:m], {v: c[k:m] for v, c in cols.items()}, m - k
                if m:
                    self.buf.extend(tt[:m], {v: c[:m] for v, c in cols.items()})
                    last = tt[m - 1]
                time.sleep(self.interval)
        except Exception as e:   # 연결 실패·끊김은 GUI에서 표시
            self.error = e
            self.running.clear()

    def stop(self):
        self.running.clear()
