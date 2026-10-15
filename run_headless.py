"""Headless demo + self-test for the mining simulator (no GUI).

Examples:
    python run_headless.py --seconds 15 --zeros 2 --delay 40
    python run_headless.py --seconds 20 --zeros 3 --delay 20
    python run_headless.py --self-test

Prints a live line once per second and a final JSON report including
chain-integrity verification.
"""

import argparse
import json
import time

from blockchain import Blockchain
from config import DEFAULT_DELAY_MS, DEFAULT_ZEROS, INITIAL_TX_COUNT
from miners import CPUMiner, GPUMiner


def main() -> int:
    ap = argparse.ArgumentParser(description="PoW mining simulator (headless demo)")
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--zeros", type=int, default=DEFAULT_ZEROS)
    ap.add_argument("--delay", type=int, default=DEFAULT_DELAY_MS)
    ap.add_argument("--gpu-lanes", type=int, default=4)
    ap.add_argument("--seed-tx", type=int, default=INITIAL_TX_COUNT)
    ap.add_argument("--self-test", action="store_true",
                    help="same run, but exit code reflects chain integrity")
    args = ap.parse_args()

    chain = Blockchain(args.zeros)
    chain.seed_mempool(args.seed_tx)
    log = lambda tag, msg: print(f"[{tag}] {msg}")
    cpu = CPUMiner(chain, args.delay, log)
    gpu = GPUMiner(chain, args.delay, log, lanes=args.gpu_lanes)
    cpu.start()
    gpu.start()

    start = time.perf_counter()
    last_report = start
    try:
        while time.perf_counter() - start < args.seconds:
            time.sleep(0.3)
            now = time.perf_counter()
            if now - last_report >= 1.0:
                last_report = now
                print(
                    f"  [live] height={len(chain.blocks) - 1} zeros={chain.difficulty_zeros} "
                    f"cpu hash={cpu.last_hash[:16]}... gpu hash={gpu.last_hash[:16]}..."
                )
    finally:
        cpu.stop()
        gpu.stop()
        cpu.join(1)
        gpu.join(1)

    elapsed = time.perf_counter() - start
    ok = chain.integrity_ok()
    report = {
        "run_seconds": round(elapsed, 2),
        "blocks_mined": len(chain.blocks) - 1,
        "difficulty_leading_zeros": chain.difficulty_zeros,
        "mempool_size": len(chain.mempool),
        "cpu": {
            "attempts": cpu.attempts,
            "blocks_found": cpu.blocks_found,
            "reward_btc": cpu.reward_btc,
            "hashrate_hps": round(cpu.hashrate(), 2),
        },
        "gpu": {
            "lanes": gpu.lanes,
            "attempts": gpu.attempts,
            "blocks_found": gpu.blocks_found,
            "reward_btc": gpu.reward_btc,
            "hashrate_hps": round(gpu.hashrate(), 2),
        },
        "integrity_ok": ok,
        "chain": [
            {
                "index": b.index,
                "nonce": b.nonce,
                "hash": b.hash[:24],
                "prev_hash": b.prev_hash[:24],
                "mined_by": b.mined_by,
                "txs": len(b.txs),
            }
            for b in chain.blocks
        ],
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.self_test:
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
