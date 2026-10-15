"""CPU and GPU miner threads.

Real GPU miners run thousands of CUDA/OpenCL cores; here we simulate
parallelism with a few lanes, each scanning a disjoint nonce stream
(nonce = lane + k * lanes). The per-attempt sleep keeps real CPU usage
near zero, so this is a process demo, not a production miner.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

from blockchain import Blockchain
from config import BLOCK_REWARD_BTC, CPU_LANES, GPU_LANES


class Miner:
    def __init__(self, name: str, lanes: int, chain: Blockchain,
                 delay_ms: int, log: Callable[[str, str], None]):
        self.name = name
        self.lanes = lanes
        self.chain = chain
        self.delay_ms = delay_ms
        self.log = log
        self._pause = threading.Event()      # set => paused
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.attempts = 0
        self.last_nonce = 0
        self.last_hash = ""
        self.blocks_found = 0
        self.reward_btc = 0.0
        self.started_at = time.perf_counter()
        self._threads = [
            threading.Thread(target=self._lane, args=(lane,), daemon=True)
            for lane in range(lanes)
        ]

    # -- lifecycle ----------------------------------------------------

    def start(self) -> None:
        for t in self._threads:
            t.start()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._pause.set()
        else:
            self._pause.clear()

    def stop(self) -> None:
        self._stop.set()
        self._pause.clear()                  # wake sleeping lanes so they can exit

    def join(self, timeout: float = 1.0) -> None:
        for t in self._threads:
            t.join(timeout)

    def hashrate(self) -> float:
        elapsed = time.perf_counter() - self.started_at
        return self.attempts / elapsed if elapsed > 0 else 0.0

    # -- proof-of-work search ------------------------------------------

    def _lane(self, lane: int) -> None:
        while not self._stop.is_set():
            # _pause set => paused; spin-wait here while paused
            while self._pause.is_set() and not self._stop.is_set():
                time.sleep(0.02)
            if self._stop.is_set():
                break
            block = self.chain.template()
            epoch = self.chain.epoch
            nonce = lane
            while not self._stop.is_set():
                block.nonce = nonce
                h = block.compute_hash(nonce)
                with self._lock:
                    self.attempts += 1
                    self.last_nonce = nonce
                    self.last_hash = h
                if int(h, 16) < block.target:
                    block.hash = h
                    if self.chain.submit(block, self.name):
                        with self._lock:
                            self.blocks_found += 1
                            self.reward_btc += BLOCK_REWARD_BTC
                        self.log(
                            "FOUND",
                            f"{self.name} 找到区块 #{block.index}  nonce={nonce}  "
                            f"hash={h[:16]}...  获得 +{BLOCK_REWARD_BTC} BTC",
                        )
                    break
                nonce += self.lanes
                if nonce >= 0xFFFFFFFF:      # 4-byte nonce exhausted
                    block.timestamp = int(time.time())   # 'extra nonce' re-roll
                    nonce = lane
                    epoch = self.chain.epoch
                if self._pause.wait(self.delay_ms / 1000):
                    break                    # pause requested
                if self.chain.epoch != epoch:
                    break                    # stale template; grab a fresh one


class CPUMiner(Miner):
    def __init__(self, chain: Blockchain, delay_ms: int,
                 log: Callable[[str, str], None]):
        super().__init__("CPU", CPU_LANES, chain, delay_ms, log)


class GPUMiner(Miner):
    def __init__(self, chain: Blockchain, delay_ms: int,
                 log: Callable[[str, str], None], lanes: int = GPU_LANES):
        super().__init__("GPU", lanes, chain, delay_ms, log)
