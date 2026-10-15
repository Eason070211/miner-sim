"""Probe: empirically check the PoW hit rate for a 4096-nonce sweep."""

from blockchain import Blockchain

chain = Blockchain(2)
chain.seed_mempool(8)
b = chain.template()
target = b.target
N = 4096
hits = 0
for n in range(N):
    h = b.compute_hash(n)
    if int(h, 16) < target:
        hits += 1
        print("hit nonce=", n, "hash=", h[:16])
print("hits =", hits, "expected ~", round(N / 256, 1))
print("target =", hex(target)[:14], "zeros =", b.zeros)
