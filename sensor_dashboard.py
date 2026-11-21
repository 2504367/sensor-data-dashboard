"""
GUI 기반 센서 데이터 통합 대시보드 (교육용)

스마트폰 phyphox 앱의 센서 데이터(가속도, 조도, 기압, 자기장 등)를
CSV 파일로 불러와서 그래프와 통계로 보여 줍니다.

실행
    python sensor_dashboard.py
    python sensor_dashboard.py data.csv          # 시작하면서 CSV 열기
"""
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

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

        self.ds = None                 # 현재 화면에 그리는 데이터
        self.ch_vars = {}
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

        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=10)

        # 왼쪽 설정 패널
        side = ttk.Frame(body, padding=(0, 0, 10, 0), width=240)
        body.add(side, weight=0)
        ttk.Label(side, text="채널", font=("", 10, "bold")).pack(anchor=tk.W)
        self.ch_frame = ttk.Frame(side)
        self.ch_frame.pack(fill=tk.X, pady=(2, 10))
        ttk.Label(self.ch_frame, text="데이터를 불러오세요", foreground="#777").pack(anchor=tk.W)

        ttk.Label(side, text="도움말", font=("", 10, "bold")).pack(anchor=tk.W, pady=(12, 0))
        ttk.Label(side, wraplength=230, foreground="#555", justify=tk.LEFT, text=(
            "phyphox 앱에서 측정한 뒤 오른쪽 위 ⋮ → '데이터 내보내기' → "
            "'CSV (쉼표, 소수점)'을 골라 PC로 옮기고 'CSV 열기'로 불러오세요.")).pack(anchor=tk.W)

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
        cols = ("unit", "mean", "std", "min", "max")
        heads = ("단위", "평균", "표준편차", "최소", "최대")
        self.table = ttk.Treeview(table_frame, columns=cols, height=5)
        self.table.heading("#0", text="채널")
        self.table.column("#0", width=200)
        for c, h in zip(cols, heads):
            self.table.heading(c, text=h)
            self.table.column(c, width=110, anchor=tk.E)
        self.table.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        self.status = tk.StringVar(value="phyphox에서 내보낸 CSV를 열어 주세요.")
        ttk.Label(self, textvariable=self.status, padding=(10, 4), relief=tk.SUNKEN, anchor=tk.W).pack(side=tk.BOTTOM, fill=tk.X)

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

    # ---------------------------------------------------------------- 갱신
    def _tick(self):
        # 설정이 바뀌었을 때만 다시 그림 (슬라이더를 움직일 때마다 그리면 느려짐)
        try:
            if self._dirty and self.ds is not None:
                self._dirty = False
                self.redraw()
        finally:
            self.after(200, self._tick)

    def redraw(self):
        ds = self.ds
        sel = [n for n, v in self.ch_vars.items() if v.get() and n in ds.channels][:MAX_PLOTS]
        self.fig.clear()
        for row in self.table.get_children():
            self.table.delete(row)
        if not sel or len(ds) < 2:
            self.canvas.draw_idle()
            return
        axes = self.fig.subplots(len(sel), 1, sharex=True, squeeze=False)[:, 0]
        step = max(1, len(ds) // MAX_DRAW_POINTS)
        t = ds.t
        for i, (ax, ch) in enumerate(zip(axes, sel)):
            y, unit = ds.channels[ch]
            ax.plot(t[::step], y[::step], color=COLORS[i % len(COLORS)], lw=1.0)
            ax.set_ylabel(f"{ch}\n({unit})" if unit else ch, fontsize=8)
            ax.grid(alpha=0.3)
            ax.tick_params(labelsize=8)
            st = sl.stats(y)
            self.table.insert("", tk.END, text=ch, values=(
                unit, *(f"{v:.4g}" for v in (st["mean"], st["std"], st["min"], st["max"]))))
        axes[-1].set_xlabel("Time (s)")
        self.fig.suptitle(ds.title, fontsize=10)
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def _on_close(self):
        self.destroy()


def main():
    app = Dashboard()
    files = [a for a in sys.argv[1:] if not a.startswith("--")]
    if files:
        app.after(300, lambda: app.open_csv(files[0]))
    app.mainloop()


if __name__ == "__main__":
    main()
