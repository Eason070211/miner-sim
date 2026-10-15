"""Minimal Bitcoin-style chain core for the simulator.

Implements the parts that matter for understanding mining:
  * the 80-byte block header (same field layout as Bitcoin),
  * double-SHA-256 hashing,
  * the target test (valid hash < target),
  * a merkle root over the transaction set,
  * a simplified difficulty adjustment loop.

Simplifications vs real Bitcoin (see README):
  * difficulty is expressed as required leading hex zeros (1..3) instead
    of the full 256-bit compact 'bits' encoding;
  * there is no P2P network, no full validation of scripts, no UTXO set.
"""

from __future__ import annotations

import hashlib
import random
import struct
import time
from dataclasses import dataclass, field

from config import BLOCK_REWARD_BTC, DEFAULT_ZEROS, DIFF_ADJUST_INTERVAL, TARGET_BLOCK_TIME_S


def double_sha256(data: bytes) -> bytes:
    """Bitcoin's hash function: SHA-256 applied twice."""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


@dataclass
class Transaction:
    txid: str
    sender: str
    receiver: str
    amount_btc: float
    fee_btc: float

    @staticmethod
    def random(tag: int) -> "Transaction":
        sender = f"addr_{random.randint(1, 9999)}"
        receiver = f"addr_{random.randint(1, 9999)}"
        amount = round(random.uniform(0.01, 2.0), 8)
        fee = round(random.uniform(0.00001, 0.0001), 8)
        payload = f"{tag}:{sender}:{receiver}:{amount}:{fee}"
        txid = double_sha256(payload.encode()).hex()
        return Transaction(txid, sender, receiver, amount, fee)

    def summary(self) -> str:
        return f"{self.sender} -> {self.receiver}  {self.amount_btc} BTC (+{self.fee_btc:.5f})"


def merkle_root(txs: list[Transaction]) -> str:
    """Binary merkle root over txids (display byte order)."""
    digests = [bytes.fromhex(t.txid) for t in txs]
    if not digests:
        digests = [bytes(32)]
    while len(digests) > 1:
        if len(digests) % 2 == 1:
            digests.append(digests[-1])
        digests = [double_sha256(a + b) for a, b in zip(digests[::2], digests[1::2])]
    return digests[0][::-1].hex()


@dataclass
class Block:
    index: int
    prev_hash: str
    txs: list[Transaction]
    zeros: int                       # demo difficulty: required leading hex zeros
    timestamp: int = field(default_factory=lambda: int(time.time()))
    nonce: int = 0
    hash: str = ""
    mined_by: str = ""

    @property
    def target(self) -> int:
        # hash < target  <=>  the first `zeros` hex digits of the hash are 0
        # (exactly 1/16^zeros per attempt)
        return int("0" * (self.zeros - 1) + "1" + "0" * (64 - self.zeros), 16)

    def header_bytes(self, nonce: int) -> bytes:
        """80-byte header; field order matches Bitcoin's block header."""
        return (
            struct.pack("<I", 1)                       # version (4 bytes)
            + bytes.fromhex(self.prev_hash)[::-1]     # previous block hash (32)
            + bytes.fromhex(merkle_root(self.txs))[::-1]  # merkle root (32)
            + struct.pack("<I", self.timestamp)       # time (4)
            + struct.pack("<I", self.zeros)           # simplified 'bits' (4)
            + struct.pack("<I", nonce)                # nonce (4)
        )

    def compute_hash(self, nonce: int) -> str:
        digest = double_sha256(self.header_bytes(nonce))
        return digest[::-1].hex()                     # display byte order

    def is_valid(self) -> bool:
        return bool(self.hash) and int(self.hash, 16) < self.target


class Blockchain:
    """Chain of blocks + mempool + difficulty bookkeeping."""

    def __init__(self, zeros: int = DEFAULT_ZEROS):
        self.difficulty_zeros = zeros
        self.epoch = 0                     # bumped every accepted block
        self.mempool: list[Transaction] = []
        self.blocks: list[Block] = []
        self.block_times: list[float] = []
        self._last_block_at = time.perf_counter()
        self.genesis()

    def genesis(self) -> None:
        g = Block(0, "0" * 64, [], self.difficulty_zeros)
        g.hash = g.compute_hash(0)
        g.mined_by = "genesis"
        self.blocks.append(g)

    def seed_mempool(self, count: int, start_tag: int = 0) -> None:
        for i in range(count):
            self.mempool.append(Transaction.random(start_tag + i))

    def head(self) -> Block:
        return self.blocks[-1]

    def template(self) -> Block:
        """A fresh, unhashed block for miners to work on."""
        return Block(
            index=len(self.blocks),
            prev_hash=self.head().hash,
            txs=list(self.mempool),
            zeros=self.difficulty_zeros,
        )

    def submit(self, block: Block, miner_name: str) -> bool:
        """Validate and append a mined block. False => stale or invalid."""
        if block.prev_hash != self.head().hash:
            return False
        if not block.is_valid() or block.index != len(self.blocks):
            return False
        block.mined_by = miner_name
        now = time.perf_counter()
        self.block_times.append(now - self._last_block_at)
        self._last_block_at = now
        self.blocks.append(block)
        self.epoch += 1
        confirmed = {t.txid for t in block.txs}
        self.mempool = [t for t in self.mempool if t.txid not in confirmed]
        self.maybe_adjust_difficulty()
        return True

    def maybe_adjust_difficulty(self) -> bool:
        """Simplified re-target: steer average block time toward
        TARGET_BLOCK_TIME_S by +/-1 leading zero. Only moves when the
        window is clearly too fast or too slow (avoids oscillation)."""
        if len(self.block_times) < DIFF_ADJUST_INTERVAL:
            return False
        window = self.block_times[-DIFF_ADJUST_INTERVAL:]
        avg = sum(window) / len(window)
        old = self.difficulty_zeros
        if avg < TARGET_BLOCK_TIME_S * 0.25 and self.difficulty_zeros < 4:
            self.difficulty_zeros += 1
        elif avg > TARGET_BLOCK_TIME_S * 2.0 and self.difficulty_zeros > 1:
            self.difficulty_zeros -= 1
        return self.difficulty_zeros != old

    def total_reward(self) -> float:
        return (len(self.blocks) - 1) * BLOCK_REWARD_BTC

    def integrity_ok(self) -> bool:
        """Recompute every link and every hash; must all check out."""
        for i in range(1, len(self.blocks)):
            b, prev = self.blocks[i], self.blocks[i - 1]
            if b.prev_hash != prev.hash or not b.is_valid():
                return False
            if b.compute_hash(b.nonce) != b.hash:
                return False
        return True
