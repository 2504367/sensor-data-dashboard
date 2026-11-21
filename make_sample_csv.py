"""
phyphox에서 내보낸 것과 같은 형식의 예제 CSV 만들기

실제 폰으로 측정하기 전에 대시보드를 시험해 볼 수 있게, 물리적으로 그럴듯한 데이터를 만듭니다.

    sample_pendulum.csv : 폰을 줄에 매달아 흔든 진자 (가속도, 주기 약 1.6 s, 이상치 몇 개 포함)
    sample_stairs.csv   : 폰을 들고 계단 3층을 올라감 (기압, 약 9 m 상승)
    sample_light.csv    : 손으로 조도 센서를 가렸다 뗐다 함 (조도)

사용: python make_sample_csv.py
"""
import csv
import math

import numpy as np

rng = np.random.default_rng(7)


def write(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_NONNUMERIC)
        w.writerow(header)
        for r in rows:
            w.writerow([float(f"{v:.9E}") for v in r])
    print(f"저장: {path} ({len(rows)}행)")


def pendulum(path="sample_pendulum.csv", rate=100.0, dur=30.0, length=0.65):
    # 단진자 주기 T = 2π√(L/g) → L = 0.65 m 이면 약 1.62 s, 주파수 약 0.62 Hz
    # 폰이 흔들리면 줄 방향(y) 가속도는 주기의 절반마다 커짐 → y축 주파수는 2배(약 1.24 Hz)
    g = 9.81
    w = math.sqrt(g / length)
    rows = []
    for i in range(int(rate * dur)):
        t = i / rate
        th0 = 0.35 * math.exp(-t / 60)
        th = th0 * math.cos(w * t)
        om = -th0 * w * math.sin(w * t)
        ax = -g * math.sin(th) * 0.15 + rng.normal(0, 0.05)              # 접선 방향 (폰 흔들림이 섞임)
        ay = g * math.cos(th) + length * om ** 2 + rng.normal(0, 0.06)   # 줄 방향: 중력 + 구심가속도
        az = rng.normal(0, 0.05)
        if rng.random() < 0.004:
            ay += rng.choice([-1, 1]) * rng.uniform(5, 12)               # 센서 순간 튐 (이상치)
        rows.append([t, ax, ay, az, math.sqrt(ax * ax + ay * ay + az * az)])
    write(path, ["Time (s)", "Acceleration x (m/s^2)", "Acceleration y (m/s^2)", "Acceleration z (m/s^2)",
                 "Absolute acceleration (m/s^2)"], rows)


def stairs(path="sample_stairs.csv", rate=10.0, dur=90.0):
    # 해수면 근처에서 높이 1 m 오를 때 기압은 약 0.12 hPa 낮아짐
    p0, rows, h = 1012.80, [], 0.0
    for i in range(int(rate * dur)):
        t = i / rate
        floor_t = [(10, 25), (35, 50), (60, 75)]                          # 계단 오르는 구간 (각 3 m)
        climbing = any(a <= t < b for a, b in floor_t)
        if climbing:
            h += 3.0 / (15 * rate)
        p = p0 * (1 - h / 44330.0) ** (1 / 0.1903) + rng.normal(0, 0.006)
        if rng.random() < 0.003:
            p += rng.choice([-1, 1]) * 0.4
        rows.append([t, p])
    write(path, ["Time (s)", "Pressure (hPa)"], rows)


def light(path="sample_light.csv", rate=20.0, dur=40.0):
    rows = []
    for i in range(int(rate * dur)):
        t = i / rate
        covered = (int(t / 4) % 2) == 1                                  # 4초마다 가림/뗌
        lx = (25 if covered else 420) + rng.normal(0, 4 if covered else 10)
        rows.append([t, max(lx, 0)])
    write(path, ["Time (s)", "Illuminance (lx)"], rows)


if __name__ == "__main__":
    pendulum()
    stairs()
    light()
