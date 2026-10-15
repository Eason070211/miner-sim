"""Configuration for the PoW mining simulator.

This is an educational, local-only simulation. It never connects to a
mining pool, never listens on a public interface, and never performs
sustained hashing. See README.md for details and limits.
"""

HOST = "127.0.0.1"            # loopback only (user requirement)
STATS_PORT = 8332             # read-only local stats endpoint; 8332 is
                              # Bitcoin Core's mainnet RPC port (teaching tie-in)
STATS_PORT_RANGE = 5          # fallback slots if 8332 is busy

BLOCK_REWARD_BTC = 3.125      # post-April-2024 halving era (valid until ~2028)
TARGET_BLOCK_TIME_S = 6.0     # demo target: one block per ~6 s
DIFF_ADJUST_INTERVAL = 3      # re-target every N blocks (BTC uses 2016)

DIFFICULTY_PRESETS = {
    "Easy (1 leading zero)": 1,
    "Medium (2 leading zeros)": 2,
    "Hard (3 leading zeros)": 3,
}
DEFAULT_ZEROS = 2

DEFAULT_DELAY_MS = 60         # animation throttle; keeps real CPU ~0%
MIN_DELAY_MS = 10
MAX_DELAY_MS = 1000

CPU_LANES = 1                 # CPU miner scans a single nonce stream
GPU_LANES = 4                 # GPU miner simulates 4 parallel cores

INITIAL_TX_COUNT = 8
MAX_MEMPOOL_TX = 20
TX_INTERVAL_S = 2.5           # a random new transaction every ~2.5 s
