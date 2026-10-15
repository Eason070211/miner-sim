"""Minimal real computation example: double-SHA-256 of an 80-byte header.

Run:  python example_hash.py

Shows how changing only the nonce produces a completely different hash -
this is the essence of proof of work. Each attempt costs ~microseconds;
the simulator adds a sleep so the search becomes visible. Real Bitcoin
currently requires the hash to start with ~19 leading hex zeros.
"""

import time

from blockchain import Blockchain


def main() -> None:
    chain = Blockchain(zeros=2)
    chain.seed_mempool(3)
    block = chain.template()
    print("Block #1 template (exactly what miners hash):")
    print(f"  prev_hash   = {block.prev_hash}")
    print(f"  target      = 0x{block.target:064x}   (valid hash must be < target)")
    print(f"  target rule = the hash must start with {block.zeros} hex zero(s)")
    print()
    for nonce in (0, 1, 2, 123456789):
        h = block.compute_hash(nonce)
        print(f"  nonce={nonce:<9}  sha256(sha256(header)) = {h}")
    print()
    print("Notice: one nonce change -> a completely different hash.")
    print("Mining = iterating the nonce until hash < target (brute force).")


if __name__ == "__main__":
    main()
