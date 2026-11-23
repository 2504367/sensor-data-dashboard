"""
GUI 기반 센서 데이터 통합 대시보드 (교육용)

스마트폰 phyphox 앱의 센서 데이터(가속도, 조도, 기압, 자기장 등)를
CSV 파일로 불러오거나 실시간으로 받아서 그래프와 통계로 보여 줍니다.

실행
    python sensor_dashboard.py
    python sensor_dashboard.py data.csv          # 시작하면서 CSV 열기
    python sensor_dashboard.py --demo            # 시뮬레이션 데이터로 바로 시작
"""
import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
import matplotlib
from matplotlib import font_manager
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

import sensorlib as sl

# 한글 글꼴 (윈도우: 맑은 고딕, 맥: 애플고딕). 없으면 기본 글꼴
_avail = {f.name for f in font_manager.fontManager.ttflist}
for _f in ("Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR"):
    if _f in _avail:
        matplotlib.rcParams["font.family"] = _f
        break
matplotlib.rcParams["axes.unicode_minus"] = False

MAX_PLOTS = 6
MAX_DRAW_POINTS = 4000       # 그래프에 그릴 최대 점 수 (많으면 솎아서 그림)
COLORS = ["#2a5bd7", "#d9532c", "#1c8a5a", "#8e44ad", "#c48a00", "#16808f", "#7f8c8d"]


class Dashboard(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("센서 데이터 통합 대시보드")
        self.geometry("1280x820")
        self.minsize(1000, 640)

        self.ds = None                 # 현재 화면에 그리는 데이터 (CSV 또는 실시간 스냅숏)
        self.source = None             # 실시간 수집 스레드
        self.buf = sl.LiveBuffer()
        self.rec_start = None          # 기록 시작 시각 (실시간 시간축 기준)
        self.recorded = None           # 기록 정지 후 확정된 구간
        self.ch_vars = {}
        self.processed = {}
        self.summary_rows = []
        self._dirty = True

        self._build_ui()
        self.after(150, self._tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Treeview", rowheight=24)

        bar = ttk.Frame(self, padding=(10, 8))
        bar.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(bar, text="CSV 열기", command=self.open_csv).pack(side=tk.LEFT)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Label(bar, text="phyphox 주소").pack(side=tk.LEFT)
        self.addr = tk.StringVar(value="192.168.0.10:8080")
        ttk.Entry(bar, textvariable=self.addr, width=20).pack(side=tk.LEFT, padx=4)
        self.btn_connect = ttk.Button(bar, text="실시간 연결", command=self.connect_phyphox)
        self.btn_connect.pack(side=tk.LEFT)
        self.btn_demo = ttk.Button(bar, text="데모(시뮬레이션)", command=self.start_demo)
        self.btn_demo.pack(side=tk.LEFT, padx=4)
        self.btn_stop = ttk.Button(bar, text="수집 중지", command=self.stop_live, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        self.btn_rec = ttk.Button(bar, text="● 기록 시작", command=self.toggle_record, state=tk.DISABLED)
        self.btn_rec.pack(side=tk.LEFT)
        ttk.Button(bar, text="엑셀로 내보내기", command=self.export).pack(side=tk.LEFT, padx=4)

        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=10)

        # 왼쪽 설정 패널
        side = ttk.Frame(body, padding=(0, 0, 10, 0), width=240)
        body.add(side, weight=0)
        ttk.Label(side, text="채널", font=("", 10, "bold")).pack(anchor=tk.W)
        self.ch_frame = ttk.Frame(side)
        self.ch_frame.pack(fill=tk.X, pady=(2, 10))
        ttk.Label(self.ch_frame, text="데이터를 불러오세요", foreground="#777").pack(anchor=tk.W)

        ttk.Label(side, text="처리", font=("", 10, "bold")).pack(anchor=tk.W)
        self.v_outlier = tk.BooleanVar(value=True)
        ttk.Checkbutton(side, text="이상치 제거 (Hampel)", variable=self.v_outlier, command=self._mark).pack(anchor=tk.W)
        self.v_hampel = tk.IntVar(value=7)
        self._slider(side, "이상치 판단 창", self.v_hampel, 3, 31)
        self.v_smooth = tk.IntVar(value=5)
        self._slider(side, "이동평균 창", self.v_smooth, 1, 51)
        self.v_raw = tk.BooleanVar(value=True)
        ttk.Checkbutton(side, text="원본도 흐리게 표시", variable=self.v_raw, command=self._mark).pack(anchor=tk.W)
        self.v_alt = tk.BooleanVar(value=False)
        ttk.Checkbutton(side, text="기압 → 상대 고도(m) 변환", variable=self.v_alt, command=self._mark).pack(anchor=tk.W)

        ttk.Label(side, text="실시간 표시", font=("", 10, "bold")).pack(anchor=tk.W, pady=(12, 0))
        self.v_window = tk.IntVar(value=20)
        self._slider(side, "최근 몇 초", self.v_window, 5, 120)

        ttk.Label(side, text="도움말", font=("", 10, "bold")).pack(anchor=tk.W, pady=(12, 0))
        ttk.Label(side, wraplength=230, foreground="#555", justify=tk.LEFT, text=(
            "phyphox 앱에서 실험을 고르고 오른쪽 위 ⋮ → '원격 접속 허용'을 켜면 "
            "화면에 주소가 나와요. PC와 폰이 같은 와이파이에 있어야 해요.")).pack(anchor=tk.W)

        # 가운데 그래프 + 아래 통계
        right = ttk.PanedWindow(body, orient=tk.VERTICAL)
        body.add(right, weight=1)
        plot_frame = ttk.Frame(right)
        right.add(plot_frame, weight=4)
        self.fig = Figure(figsize=(8, 6), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        NavigationToolbar2Tk(self.canvas, plot_frame).update()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        table_frame = ttk.Frame(right)
        right.add(table_frame, weight=1)
        cols = ("unit", "mean", "std", "min", "max", "freq", "outliers")
        heads = ("단위", "평균", "표준편차", "최소", "최대", "주 주파수 (Hz)", "제거한 이상치")
        self.table = ttk.Treeview(table_frame, columns=cols, height=5)
        self.table.heading("#0", text="채널")
        self.table.column("#0", width=200)
        for c, h in zip(cols, heads):
            self.table.heading(c, text=h)
            self.table.column(c, width=110, anchor=tk.E)
        self.table.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        self.status = tk.StringVar(value="CSV를 열거나, phyphox에 연결하거나, 데모를 시작하세요.")
        ttk.Label(self, textvariable=self.status, padding=(10, 4), relief=tk.SUNKEN, anchor=tk.W).pack(side=tk.BOTTOM, fill=tk.X)

    def _slider(self, parent, label, var, lo, hi):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=1)
        ttk.Label(row, text=label).pack(side=tk.LEFT)
        val = ttk.Label(row, width=4, anchor=tk.E)
        val.pack(side=tk.RIGHT)

        def on(_=None):
            v = int(round(float(scale.get())))
            var.set(v)
            val.config(text=str(v))
            self._mark()
        scale = ttk.Scale(parent, from_=lo, to=hi, command=on)
        scale.set(var.get())
        scale.pack(fill=tk.X)
        val.config(text=str(var.get()))

    def _mark(self):
        self._dirty = True

    # ------------------------------------------------------------ 데이터 소스
    def open_csv(self, path=None):
        path = path or filedialog.askopenfilename(title="phyphox CSV 열기", filetypes=[("CSV", "*.csv"), ("모든 파일", "*.*")])
        if not path:
            return
        try:
            ds = sl.load_csv(path)
        except Exception as e:
            messagebox.showerror("불러오기 실패", str(e))
            return
        self.stop_live()
        self.recorded = None
        self._set_dataset(ds)
        dur = ds.t[-1] - ds.t[0] if len(ds) else 0
        rate = (len(ds) - 1) / dur if dur > 0 else 0
        self.status.set(f"{ds.title}: {len(ds)}개 점, {dur:.1f} s, 약 {rate:.0f} Hz, 채널 {len(ds.channels)}개")

    def _set_dataset(self, ds, keep_selection=False):
        self.ds = ds
        names = ds.names()
        if not keep_selection or set(names) != set(self.ch_vars):
            for w in self.ch_frame.winfo_children():
                w.destroy()
            old = {k: v.get() for k, v in self.ch_vars.items()}
            self.ch_vars = {}
            for i, n in enumerate(names):
                default = old.get(n, i < 4 and "absolute" not in n.lower())
                v = tk.BooleanVar(value=default)
                unit = ds.channels[n][1]
                ttk.Checkbutton(self.ch_frame, text=f"{n} [{unit}]" if unit else n, variable=v, command=self._mark).pack(anchor=tk.W)
                self.ch_vars[n] = v
        self._mark()

    def start_demo(self):
        self._start_source(sl.SimulatedSource(self.buf), "시뮬레이션 데이터 수집 중 (50 Hz)")

    def connect_phyphox(self):
        self._start_source(sl.PhyphoxSource(self.buf, self.addr.get()), f"{self.addr.get()} 연결 중…")

    def _start_source(self, src, msg):
        self.stop_live()
        self.recorded, self.rec_start = None, None
        self.source = src
        src.start()
        self.btn_stop.config(state=tk.NORMAL)
        self.btn_rec.config(state=tk.NORMAL, text="● 기록 시작")
        self.btn_connect.config(state=tk.DISABLED)
        self.btn_demo.config(state=tk.DISABLED)
        self.status.set(msg)

    def stop_live(self):
        if self.source:
            self.source.stop()
            self.source = None
            if self.rec_start is not None:
                self.toggle_record()
            self.status.set("수집을 멈췄어요. 그래프는 마지막 데이터를 보여 줘요.")
        self.btn_stop.config(state=tk.DISABLED)
        self.btn_rec.config(state=tk.DISABLED)
        self.btn_connect.config(state=tk.NORMAL)
        self.btn_demo.config(state=tk.NORMAL)

    def toggle_record(self):
        snap = self.buf.snapshot()
        now = snap.t[-1] if len(snap) else 0.0
        if self.rec_start is None:
            self.rec_start = now
            self.recorded = None
            self.btn_rec.config(text="■ 기록 정지")
            self.status.set(f"기록 중… (t = {now:.1f} s부터)")
        else:
            ds = self.buf.snapshot(title="recording", since=self.rec_start)
            ds = sl.Dataset(ds.title, ds.t[ds.t <= now], {k: (v[:np.sum(ds.t <= now)], u) for k, (v, u) in ds.channels.items()})
            self.recorded = ds
            self.rec_start = None
            self.btn_rec.config(text="● 기록 시작")
            self.status.set(f"기록 완료: {len(ds)}개 점, {ds.t[-1]-ds.t[0]:.1f} s. '엑셀로 내보내기'로 저장할 수 있어요." if len(ds) > 1 else "기록된 데이터가 없어요.")

    # ---------------------------------------------------------------- 갱신
    def _tick(self):
        try:
            if self.source is not None:
                err = getattr(self.source, "error", None)
                if err:
                    self.stop_live()
                    messagebox.showerror("연결 오류", f"phyphox에 연결하지 못했어요.\n\n{err}\n\n"
                                         "폰과 PC가 같은 와이파이인지, 앱에서 원격 접속을 켰는지, 주소가 맞는지 확인해 주세요.")
                elif self.source.running.is_set():
                    snap = self.buf.snapshot(title=getattr(self.source, "title", "live"))
                    if len(snap):
                        if len(snap.t) and snap.t[-1] > self.v_window.get():
                            snap = self._window(snap, snap.t[-1] - self.v_window.get())
                        self._set_dataset(snap, keep_selection=True)
                        if self.rec_start is not None:
                            self.status.set(f"기록 중… {snap.t[-1] - self.rec_start:.1f} s")
            if self._dirty and self.ds is not None:
                self._dirty = False
                self.redraw()
        finally:
            self.after(200, self._tick)

    @staticmethod
    def _window(ds, t_from):
        m = ds.t >= t_from
        return sl.Dataset(ds.title, ds.t[m], {k: (v[m], u) for k, (v, u) in ds.channels.items()})

    def redraw(self):
        ds = self.ds
        sel = [n for n, v in self.ch_vars.items() if v.get() and n in ds.channels][:MAX_PLOTS]
        self.fig.clear()
        self.processed, self.summary_rows = {}, []
        for row in self.table.get_children():
            self.table.delete(row)
        if not sel or len(ds) < 2:
            self.canvas.draw_idle()
            return
        axes = self.fig.subplots(len(sel), 1, sharex=True, squeeze=False)[:, 0]
        step = max(1, len(ds) // MAX_DRAW_POINTS)
        t = ds.t
        for i, (ax, ch) in enumerate(zip(axes, sel)):
            p = sl.process(ds, ch, outlier=self.v_outlier.get(), hampel_window=self.v_hampel.get(),
                           smooth=self.v_smooth.get(), altitude=self.v_alt.get())
            self.processed[ch] = p
            col = COLORS[i % len(COLORS)]
            if self.v_raw.get():
                ax.plot(t[::step], p["raw"][::step], color=col, alpha=0.25, lw=0.8)
            ax.plot(t[::step], p["smooth"][::step], color=col, lw=1.4)
            if p["outliers"].any():
                ax.plot(t[p["outliers"]], p["raw"][p["outliers"]], "x", color="#c0392b", ms=5)
            ax.set_ylabel(f"{p['label']}\n({p['unit']})" if p["unit"] else p["label"], fontsize=8)
            ax.grid(alpha=0.3)
            ax.tick_params(labelsize=8)
            st = sl.stats(p["smooth"])
            f = sl.dominant_frequency(t, p["clean"])
            n_out = int(p["outliers"].sum())
            row = [p["label"], p["unit"], st["mean"], st["std"], st["min"], st["max"], f, n_out]
            self.summary_rows.append(row)
            self.table.insert("", tk.END, text=p["label"], values=(
                p["unit"], *(f"{v:.4g}" for v in (st["mean"], st["std"], st["min"], st["max"])),
                "–" if np.isnan(f) else f"{f:.3f}", n_out))
        axes[-1].set_xlabel("Time (s)")
        self.fig.suptitle(ds.title, fontsize=10)
        self.fig.tight_layout()
        self.canvas.draw_idle()

    # -------------------------------------------------------------- 내보내기
    def export(self):
        ds = self.recorded or self.ds
        if ds is None or len(ds) < 2:
            messagebox.showinfo("내보내기", "내보낼 데이터가 없어요.")
            return
        sel = [n for n, v in self.ch_vars.items() if v.get() and n in ds.channels] or ds.names()
        path = filedialog.asksaveasfilename(title="저장", defaultextension=".xlsx",
                                            initialfile=os.path.splitext(ds.title)[0] + "_분석",
                                            filetypes=[("Excel", "*.xlsx"), ("CSV", "*.csv")])
        if not path:
            return
        try:
            processed, rows = self.build_export(ds, sel)
            sl.export(path, ds, processed, rows)
        except Exception as e:
            messagebox.showerror("저장 실패", str(e))
            return
        self.status.set(f"저장했어요: {path}")

    def build_export(self, ds, sel):
        processed, rows = {}, []
        for ch in sel:
            p = sl.process(ds, ch, outlier=self.v_outlier.get(), hampel_window=self.v_hampel.get(),
                           smooth=self.v_smooth.get(), altitude=self.v_alt.get())
            processed[ch] = p
            st = sl.stats(p["smooth"])
            rows.append([p["label"], p["unit"], st["mean"], st["std"], st["min"], st["max"],
                         sl.dominant_frequency(ds.t, p["clean"]), int(p["outliers"].sum())])
        return processed, rows

    def _on_close(self):
        if self.source:
            self.source.stop()
        self.destroy()


def main():
    app = Dashboard()
    args = sys.argv[1:]
    if "--demo" in args:
        app.after(300, app.start_demo)
    files = [a for a in args if not a.startswith("--")]
    if files:
        app.after(300, lambda: app.open_csv(files[0]))
    app.mainloop()


if __name__ == "__main__":
    main()
