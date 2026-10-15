"""PoW mining simulator - Tkinter GUI.

Educational demo (per user scope): loopback only, no outbound network,
no pool connection, no sustained hashing. Real PoW hashes of an 80-byte
header are computed once per animation tick (microseconds each), then
the loop sleeps, so real CPU usage stays ~0%.

Run:  python main.py
A read-only stats endpoint is also served at http://127.0.0.1:8332/stats
"""

from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tkinter import ttk

from blockchain import Blockchain, Transaction
from config import (
    BLOCK_REWARD_BTC,
    DEFAULT_DELAY_MS,
    DEFAULT_ZEROS,
    DIFF_ADJUST_INTERVAL,
    DIFFICULTY_PRESETS,
    HOST,
    INITIAL_TX_COUNT,
    MAX_DELAY_MS,
    MAX_MEMPOOL_TX,
    MIN_DELAY_MS,
    STATS_PORT,
    STATS_PORT_RANGE,
    TARGET_BLOCK_TIME_S,
    TX_INTERVAL_S,
)
from miners import CPUMiner, GPUMiner

FONT = ("Microsoft YaHei UI", 10)
FONT_BOLD = ("Microsoft YaHei UI", 10, "bold")
MONO = ("Consolas", 9)

LOG_COLORS = {
    "SYS": "#475569",
    "TX": "#1d4ed8",
    "FOUND": "#15803d",
    "DIFF": "#b45309",
    "ERR": "#b91c1c",
}

GENESIS_FILL = "#e2e8f0"
BLOCK_FILL = "#ffffff"
NEW_FILL = "#d1fae5"
LINK_COLOR = "#94a3b8"


def fmt_btc(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".")


# ----------------------------------------------------------------------
# localhost stats endpoint
# ----------------------------------------------------------------------

class StatsServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class StatsHandler(BaseHTTPRequestHandler):
    app: "MinerSimApp" = None

    def do_GET(self):  # noqa: N802
        if self.path in ("/", "/index.html"):
            body = (
                "<html><meta charset='utf-8'><title>mining sim</title>"
                "<body style='font-family:sans-serif;margin:2rem'>"
                "<h2>PoW Mining Simulator (localhost only)</h2>"
                "<p>Read-only stats endpoint for the Tkinter demo.</p>"
                "<p><a href='/stats'>/stats</a> (JSON)</p></body></html>"
            ).encode("utf-8")
            self.send_response(200)
        elif self.path == "/stats":
            body = json.dumps(self.app.stats(), indent=2).encode("utf-8")
            self.send_response(200)
        else:
            body = b"not found"
            self.send_response(404)
        ctype = "application/json" if self.path == "/stats" else "text/html; charset=utf-8"
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep console clean
        pass


# ----------------------------------------------------------------------
# application
# ----------------------------------------------------------------------

class MinerSimApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("PoW 挖矿模拟器 — 教学演示(仅本地运行)")
        self.root.geometry("1360x860")
        self.log_q: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self.closing = False
        self.paused = False
        self.delay_ms = DEFAULT_DELAY_MS
        self._t0 = time.perf_counter()
        self._port_text = ""

        self.chain = Blockchain(DEFAULT_ZEROS)
        self.chain.seed_mempool(INITIAL_TX_COUNT)

        self._build_ui()

        self.cpu = CPUMiner(self.chain, self.delay_ms, self._log_cb)
        self.gpu = GPUMiner(self.chain, self.delay_ms, self._log_cb)
        self.cpu.start()
        self.gpu.start()

        self._start_tx_generator()
        self._start_stats_server()

        self._last_chain_len = len(self.chain.blocks)
        self._last_zeros = self.chain.difficulty_zeros
        self._mempool_key: tuple = ()
        self._redraw_chain()
        self._update_block_detail()
        self._update_status()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll()

    # -- logging -------------------------------------------------------

    def _log_cb(self, tag: str, msg: str) -> None:
        self.log_q.put((tag, msg))

    # -- ui construction -----------------------------------------------

    def _build_ui(self) -> None:
        bar = ttk.Frame(self.root, padding=(10, 8))
        bar.pack(side="top", fill="x")

        self.btn_toggle = ttk.Button(bar, text="暂停", command=self._toggle_pause)
        self.btn_toggle.pack(side="left")
        ttk.Button(bar, text="重置", command=self._reset).pack(side="left", padx=(6, 14))

        ttk.Label(bar, text="难度:").pack(side="left")
        self.combo_zeros = ttk.Combobox(bar, state="readonly", width=24,
                                        values=list(DIFFICULTY_PRESETS))
        self.combo_zeros.current(list(DIFFICULTY_PRESETS.values()).index(DEFAULT_ZEROS))
        self.combo_zeros.pack(side="left")
        self.combo_zeros.bind("<<ComboboxSelected>>", self._on_zeros_change)

        ttk.Label(bar, text="  动画延迟:").pack(side="left")
        self.scale_delay = ttk.Scale(bar, from_=MIN_DELAY_MS, to=MAX_DELAY_MS,
                                     orient="horizontal", command=self._on_delay_change)
        self.scale_delay.set(self.delay_ms)
        self.scale_delay.pack(side="left", fill="x", expand=True)
        self.lbl_delay = ttk.Label(bar, text=f"{self.delay_ms} ms/次")
        self.lbl_delay.pack(side="left", padx=6)
        self.lbl_port = ttk.Label(bar, text="")
        self.lbl_port.pack(side="right")

        paned = ttk.PanedWindow(self.root, orient="horizontal")
        paned.pack(side="top", fill="both", expand=True, padx=10, pady=4)

        # left: blockchain view
        left = ttk.Frame(paned)
        ttk.Label(left, text="区块链视图(区块 + 哈希链)", font=FONT_BOLD).pack(anchor="w")
        wrap = ttk.Frame(left)
        wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, bg="#f8fafc", highlightthickness=1,
                                highlightbackground="#cbd5e1")
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        paned.add(left, weight=2)

        # center: miners
        center = ttk.Frame(paned)
        paned.add(center, weight=3)
        self.cpu_vars = self._make_miner_panel(center, "CPU 矿工 · 1 条流(顺序搜索)")
        self.gpu_vars = self._make_miner_panel(center, "GPU 矿工 · 4 条并行流(模拟 CUDA 核心)")

        # right: mempool + latest block detail
        right = ttk.Frame(paned)
        paned.add(right, weight=2)
        ttk.Label(right, text="内存池 Mempool(待确认交易)", font=FONT_BOLD).pack(anchor="w")
        self.mempool_list = tk.Listbox(right, height=6, font=MONO, activestyle="none")
        self.mempool_list.pack(fill="x")
        ttk.Label(right, text="最新区块详情", font=FONT_BOLD).pack(anchor="w", pady=(10, 2))
        self.detail_text = tk.Text(right, height=9, font=MONO, wrap="word",
                                   state="disabled", borderwidth=0, background="#f8fafc")
        self.detail_text.pack(fill="both", expand=True)

        # bottom: log
        log_frame = ttk.Frame(self.root)
        log_frame.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
        ttk.Label(log_frame, text="事件日志", font=FONT_BOLD).pack(anchor="w")
        self.log = tk.Text(log_frame, height=11, font=("Microsoft YaHei UI", 9), wrap="word",
                           state="normal")
        sb = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="x", expand=True)
        for tag, color in LOG_COLORS.items():
            self.log.tag_configure(tag, foreground=color)

        # status bar
        self.status_var = tk.StringVar()
        ttk.Label(self.root, textvariable=self.status_var, relief="sunken",
                  anchor="w", padding=(6, 2)).pack(side="bottom", fill="x")

    def _make_miner_panel(self, parent, title) -> dict:
        frame = ttk.LabelFrame(parent, text=title, padding=8)
        frame.pack(fill="x", pady=4)
        v = {
            "status": tk.StringVar(value="搜索中…"),
            "nonce": tk.StringVar(value="0"),
            "attempts": tk.StringVar(value="0"),
            "hashrate": tk.StringVar(value="0.00 H/s"),
            "found": tk.StringVar(value="0"),
            "reward": tk.StringVar(value="0"),
            "progress": ttk.Progressbar(frame, mode="indeterminate", length=180),
            "hash_text": None,
        }
        ttk.Label(frame, text="状态:").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, textvariable=v["status"], foreground="#1e40af"
                  ).grid(row=0, column=1, sticky="w")
        row = 1
        ttk.Label(frame, text="Nonce:").grid(row=row, column=0, sticky="w")
        ttk.Label(frame, textvariable=v["nonce"], font=MONO).grid(row=row, column=1, sticky="w")
        ttk.Label(frame, text="尝试次数:").grid(row=row, column=2, sticky="w", padx=(16, 0))
        ttk.Label(frame, textvariable=v["attempts"], font=MONO).grid(row=row, column=3, sticky="w")
        row += 1
        ttk.Label(frame, text="算力:").grid(row=row, column=0, sticky="w")
        ttk.Label(frame, textvariable=v["hashrate"], font=MONO).grid(row=row, column=1, sticky="w")
        ttk.Label(frame, text="已出块:").grid(row=row, column=2, sticky="w", padx=(16, 0))
        ttk.Label(frame, textvariable=v["found"], font=MONO).grid(row=row, column=3, sticky="w")
        ttk.Label(frame, text="收益:").grid(row=row, column=4, sticky="w", padx=(16, 0))
        ttk.Label(frame, textvariable=v["reward"], font=MONO).grid(row=row, column=5, sticky="w")
        row += 1
        ttk.Label(frame, text="当前哈希:").grid(row=row, column=0, sticky="w")
        ht = tk.Text(frame, height=1, font=MONO, wrap="none", state="disabled",
                     width=58, borderwidth=0, background="#f1f5f9")
        ht.grid(row=row, column=1, columnspan=4, sticky="ew")
        ht.tag_configure("zero", foreground="#15803d", font=("Consolas", 9, "bold"))
        ht.tag_configure("rest", foreground="#334155")
        v["hash_text"] = ht
        row += 1
        v["progress"].grid(row=row, column=0, columnspan=6, sticky="ew", pady=(6, 0))
        v["progress"].start(10)
        return v

    def _set_hash(self, widget: tk.Text, h: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        if not h:
            widget.insert("end", "—")
        else:
            zeros = 0
            while zeros < len(h) and h[zeros] == "0":
                zeros += 1
            widget.insert("end", h[:zeros], "zero")
            widget.insert("end", h[zeros:], "rest")
        widget.configure(state="disabled")

    # -- chain rendering ------------------------------------------------

    def _redraw_chain(self) -> None:
        self.canvas.delete("all")
        bw, bh, vgap = 400, 84, 34
        for i, b in enumerate(self.chain.blocks):
            y = 12 + i * (bh + vgap)
            is_new = i == len(self.chain.blocks) - 1 and i > 0
            fill = NEW_FILL if is_new else (GENESIS_FILL if i == 0 else BLOCK_FILL)
            outline = "#15803d" if is_new else "#94a3b8"
            self.canvas.create_rectangle(20, y, 20 + bw, y + bh, fill=fill,
                                         outline=outline, width=2)
            self.canvas.create_text(34, y + 14, anchor="w", font=MONO,
                                    text=f"Block #{b.index}  (miner: {b.mined_by})")
            self.canvas.create_text(34, y + 36, anchor="w", font=MONO,
                                    text=f"hash   {b.hash[:18]}...")
            self.canvas.create_text(34, y + 54, anchor="w", font=MONO,
                                    text=f"prev   {b.prev_hash[:18]}...")
            self.canvas.create_text(34, y + 70, anchor="w", font=MONO,
                                    text=f"txs {len(b.txs)}   nonce {b.nonce}")
            if i > 0:
                x0 = 20 + bw / 2
                y0 = 12 + (i - 1) * (bh + vgap) + bh
                y1 = 12 + i * (bh + vgap)
                self.canvas.create_line(x0, y0, x0, y1, fill=LINK_COLOR, width=2,
                                        arrow=tk.LAST)
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.yview_moveto(1.0)

    def _update_block_detail(self) -> None:
        b = self.chain.head()
        lines = [
            f"高度    : #{b.index}",
            f"Nonce   : {b.nonce}",
            f"哈希    : {b.hash}",
            f"前块哈希: {b.prev_hash}",
            f"目标    : {b.target:064x}",
            f"交易数  : {len(b.txs)}",
            f"矿工    : {b.mined_by}",
            f"时间    : {time.strftime('%H:%M:%S', time.localtime(b.timestamp))}",
        ]
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", "\n".join(lines))
        self.detail_text.configure(state="disabled")

    def _update_mempool(self) -> None:
        key = tuple(t.txid for t in self.chain.mempool)
        if key == self._mempool_key:
            return
        self._mempool_key = key
        self.mempool_list.delete(0, "end")
        for t in self.chain.mempool:
            self.mempool_list.insert("end", f"{t.txid[:8]}  {t.summary()}")

    # -- controls -------------------------------------------------------

    def _toggle_pause(self) -> None:
        self.paused = not self.paused
        self.cpu.set_paused(self.paused)
        self.gpu.set_paused(self.paused)
        self.btn_toggle.config(text="开始" if self.paused else "暂停")
        self.log_q.put(("SYS", "已暂停,矿工挂起" if self.paused else "已恢复搜索"))

    def _reset(self) -> None:
        self.cpu.stop()
        self.gpu.stop()
        self.cpu.join(1)
        self.gpu.join(1)
        self.chain = Blockchain(DEFAULT_ZEROS)
        self.chain.seed_mempool(INITIAL_TX_COUNT)
        self.cpu = CPUMiner(self.chain, self.delay_ms, self._log_cb)
        self.gpu = GPUMiner(self.chain, self.delay_ms, self._log_cb)
        self.cpu.start()
        self.gpu.start()
        self.paused = False
        self.btn_toggle.config(text="暂停")
        self._last_chain_len = len(self.chain.blocks)
        self._last_zeros = self.chain.difficulty_zeros
        self._mempool_key = ()
        self.log.delete("1.0", "end")
        self._redraw_chain()
        self._update_block_detail()
        self.log_q.put(("SYS", "已重置:新区块链 + 新内存池"))

    def _on_zeros_change(self, _event=None) -> None:
        label = self.combo_zeros.get()
        self.chain.difficulty_zeros = DIFFICULTY_PRESETS[label]
        self.log_q.put(("DIFF", f"难度已切换:{DIFFICULTY_PRESETS[label]} 个前导零(下一区块生效)"))

    def _on_delay_change(self, val: str) -> None:
        self.delay_ms = int(float(val))
        self.lbl_delay.config(text=f"{self.delay_ms} ms/次")
        self.cpu.delay_ms = self.delay_ms
        self.gpu.delay_ms = self.delay_ms

    # -- background services --------------------------------------------

    def _start_tx_generator(self) -> None:
        def loop():
            while not self.closing:
                time.sleep(TX_INTERVAL_S)
                if self.closing:
                    break
                if len(self.chain.mempool) < MAX_MEMPOOL_TX:
                    tx = Transaction.random(int(time.time()))
                    self.chain.mempool.append(tx)
                    self.log_q.put(("TX", f"新交易进入内存池: {tx.summary()}"))

        threading.Thread(target=loop, daemon=True).start()

    def _start_stats_server(self) -> None:
        StatsHandler.app = self
        port = STATS_PORT
        httpd = None
        for _ in range(STATS_PORT_RANGE):
            try:
                httpd = StatsServer((HOST, port), StatsHandler)
                break
            except OSError:
                port += 1
        if httpd is None:
            self._port_text = "端口占用,已禁用 /stats"
        else:
            self.httpd = httpd
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            self._port_text = f"http://{HOST}:{port}/stats"
        self.lbl_port.config(text=f"本地统计: {self._port_text}")

    # -- stats -----------------------------------------------------------

    @staticmethod
    def _miner_stats(m: object) -> dict:
        return {
            "attempts": m.attempts,
            "last_nonce": m.last_nonce,
            "last_hash": m.last_hash[:16],
            "hashrate_hps": round(m.hashrate(), 2),
            "blocks_found": m.blocks_found,
            "reward_btc": m.reward_btc,
        }

    def stats(self) -> dict:
        head = self.chain.head()
        return {
            "type": "pow-mining-simulator (educational, localhost only)",
            "chain_height": len(self.chain.blocks) - 1,
            "difficulty_leading_zeros": self.chain.difficulty_zeros,
            "mempool_size": len(self.chain.mempool),
            "block_reward_btc": BLOCK_REWARD_BTC,
            "total_reward_btc": self.chain.total_reward(),
            "integrity_ok": self.chain.integrity_ok(),
            "cpu": self._miner_stats(self.cpu),
            "gpu": self._miner_stats(self.gpu),
            "last_block": {
                "index": head.index,
                "nonce": head.nonce,
                "hash": head.hash,
                "mined_by": head.mined_by,
            },
        }

    # -- poll loop -------------------------------------------------------

    def _poll(self) -> None:
        if self.closing:
            return
        self._drain_log()
        self._update_miner_vars(self.cpu, self.cpu_vars)
        self._update_miner_vars(self.gpu, self.gpu_vars)
        self._update_mempool()
        if len(self.chain.blocks) != self._last_chain_len:
            self._last_chain_len = len(self.chain.blocks)
            self._redraw_chain()
            self._update_block_detail()
        if self.chain.difficulty_zeros != self._last_zeros:
            avg = sum(self.chain.block_times[-DIFF_ADJUST_INTERVAL:]) / DIFF_ADJUST_INTERVAL
            self.log_q.put(
                ("DIFF",
                 f"全网难度调整:{self._last_zeros} → {self.chain.difficulty_zeros} 个前导零"
                 f"(近 {DIFF_ADJUST_INTERVAL} 块平均 {avg:.1f}s/块,目标 {TARGET_BLOCK_TIME_S}s)"))
            self._last_zeros = self.chain.difficulty_zeros
        self._update_status()
        self.root.after(60, self._poll)

    def _drain_log(self) -> None:
        try:
            while True:
                tag, msg = self.log_q.get_nowait()
                self.log.insert("end", f"[{tag}] {msg}\n", tag)
        except queue.Empty:
            pass
        if int(self.log.index("end-1c").split(".")[0]) > 3000:
            self.log.delete("1.0", "500.0")
        self.log.see("end")

    def _update_miner_vars(self, miner, v: dict) -> None:
        v["nonce"].set(str(miner.last_nonce))
        v["attempts"].set(f"{miner.attempts:,}")
        v["hashrate"].set(f"{miner.hashrate():.2f} H/s")
        v["found"].set(str(miner.blocks_found))
        v["reward"].set(fmt_btc(miner.reward_btc))
        v["status"].set("搜索中…" if not self.paused else "已暂停")
        self._set_hash(v["hash_text"], miner.last_hash)

    def _update_status(self) -> None:
        self.status_var.set(
            f"链高度 {len(self.chain.blocks) - 1}  |  难度 {self.chain.difficulty_zeros} 个前导零  |  "
            f"全网累计出块奖励 {fmt_btc(self.chain.total_reward())} BTC  |  "
            f"运行 {int(time.perf_counter() - self._t0)} s  |  "
            f"本地统计 {self._port_text}"
        )

    # -- close ----------------------------------------------------------

    def _on_close(self) -> None:
        self.closing = True
        self.cpu.stop()
        self.gpu.stop()
        try:
            self.httpd.shutdown()
        except Exception:
            pass
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    MinerSimApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
