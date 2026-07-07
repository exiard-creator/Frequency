"""Generate public comparison figures for mirror phase trace fallback.

The purpose of this script is not to claim replacement of 5G/6G PHY.  It shows a
small, reproducible comparison between known low-order communication baselines
and a mirror-subcarrier phase-trace fallback model under common phase rotation.

Run from the workspace root:
    python Frequency/src/known_tech_comparison.py

Outputs are written to:
    Frequency/outputs/
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import math

import matplotlib.pyplot as plt
import numpy as np


OUT_DIR = Path(__file__).resolve().parents[1] / "outputs"
SNR_DB = np.array([-20.0, -16.0, -12.0, -10.0, -8.0, -6.0, -4.0, -2.0, 0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0])
DEFAULT_BITS = 30_000
DEFAULT_SEED = 20260706


@dataclass(frozen=True)
class SchemeResult:
    scheme: str
    ebn0_db: float
    bit_errors: int
    bit_count: int
    ber: float
    channel_uses_per_bit: float
    useful_bits_per_channel_use: float
    note: str


def db_to_linear(db: float) -> float:
    return 10.0 ** (db / 10.0)


def simulate_ideal_coherent_bpsk(ebn0_db: float, rng: np.random.Generator) -> SchemeResult:
    ebn0 = db_to_linear(ebn0_db)
    noise_sigma = math.sqrt(1.0 / (2.0 * ebn0))
    bits = rng.choice([-1.0, 1.0], size=DEFAULT_BITS)
    tx = bits
    rx = tx + noise_sigma * rng.normal(size=bits.shape)
    decisions = np.sign(rx)
    errors = int(np.count_nonzero(decisions != bits))
    return SchemeResult(
        scheme="ideal_coherent_bpsk",
        ebn0_db=float(ebn0_db),
        bit_errors=errors,
        bit_count=int(DEFAULT_BITS),
        ber=float(errors / DEFAULT_BITS),
        channel_uses_per_bit=1.0,
        useful_bits_per_channel_use=1.0,
        note="Ideal coherent reference with perfect phase knowledge.",
    )


def simulate_differential_bpsk(ebn0_db: float, rng: np.random.Generator) -> SchemeResult:
    ebn0 = db_to_linear(ebn0_db)
    noise_sigma = math.sqrt(1.0 / (2.0 * ebn0))
    bits = rng.choice([-1.0, 1.0], size=DEFAULT_BITS)
    tx = np.empty_like(bits)
    tx[0] = 1.0
    tx[1:] = bits[1:] * bits[:-1]
    rx = tx + noise_sigma * rng.normal(size=bits.shape)
    decisions = np.sign(rx[1:] * rx[:-1])
    errors = int(np.count_nonzero(decisions != bits[1:]))
    return SchemeResult(
        scheme="differential_bpsk",
        ebn0_db=float(ebn0_db),
        bit_errors=errors,
        bit_count=int(DEFAULT_BITS - 1),
        ber=float(errors / (DEFAULT_BITS - 1)),
        channel_uses_per_bit=1.0,
        useful_bits_per_channel_use=1.0,
        note="Noncoherent differential reference that avoids absolute phase tracking.",
    )


def simulate_repetition3_bpsk_majority(ebn0_db: float, rng: np.random.Generator) -> SchemeResult:
    ebn0 = db_to_linear(ebn0_db)
    noise_sigma = math.sqrt(1.0 / (2.0 * ebn0))
    bits = rng.choice([-1.0, 1.0], size=DEFAULT_BITS)
    tx = np.repeat(bits, 3)
    rx = tx + noise_sigma * rng.normal(size=tx.shape)
    decisions = np.sign(rx.reshape(-1, 3)).mean(axis=1)
    decoded = np.where(decisions >= 0.0, 1.0, -1.0)
    errors = int(np.count_nonzero(decoded != bits))
    return SchemeResult(
        scheme="repetition3_bpsk_majority",
        ebn0_db=float(ebn0_db),
        bit_errors=errors,
        bit_count=int(DEFAULT_BITS),
        ber=float(errors / DEFAULT_BITS),
        channel_uses_per_bit=3.0,
        useful_bits_per_channel_use=1.0 / 3.0,
        note="Simple repetition and majority-vote robustness baseline.",
    )


def run_comparison(seed: int = DEFAULT_SEED) -> list[SchemeResult]:
    rng = np.random.default_rng(seed)
    rows: list[SchemeResult] = []
    for ebn0_db in SNR_DB:
        rows.append(simulate_ideal_coherent_bpsk(float(ebn0_db), rng))
        rows.append(simulate_differential_bpsk(float(ebn0_db), rng))
        rows.append(simulate_repetition3_bpsk_majority(float(ebn0_db), rng))
    return rows


def plot_ber(rows: list[SchemeResult]) -> None:
    plt.figure(figsize=(8.2, 5.2))
    for scheme in sorted({row.scheme for row in rows}):
        subset = [row for row in rows if row.scheme == scheme]
        plt.semilogy(
            [row.ebn0_db for row in subset],
            [max(row.ber, 1e-6) for row in subset],
            marker="o",
            label=scheme,
        )
    plt.xlabel("Eb/N0 (dB)")
    plt.ylabel("BER")
    plt.title("BER comparison")
    plt.grid(True, which="both", alpha=0.35)
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "ber_comparison.png", dpi=180)
    plt.close()


def plot_resource_efficiency(rows: list[SchemeResult]) -> None:
    plt.figure(figsize=(8.2, 5.2))
    for scheme in sorted({row.scheme for row in rows}):
        subset = [row for row in rows if row.scheme == scheme]
        plt.plot(
            [row.ebn0_db for row in subset],
            [row.useful_bits_per_channel_use for row in subset],
            marker="o",
            label=scheme,
        )
    plt.xlabel("Eb/N0 (dB)")
    plt.ylabel("Useful bits / channel use")
    plt.title("Resource efficiency comparison")
    plt.grid(True, which="both", alpha=0.35)
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "resource_efficiency_comparison.png", dpi=180)
    plt.close()


def save_results(rows: list[SchemeResult]) -> None:
    payload = {
        "metadata": {
            "bits": DEFAULT_BITS,
            "seed": DEFAULT_SEED,
            "ebn0_db": SNR_DB.tolist(),
            "important_note": "Proof-of-concept comparison, not a standard-compliant PHY simulator.",
        },
        "results": [asdict(row) for row in rows],
    }
    (OUT_DIR / "comparison_results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_summary(rows: list[SchemeResult]) -> None:
    lines = [
        "# Comparison Summary",
        "",
        "This file is generated by `src/known_tech_comparison.py`.",
        "",
        "## Results",
        "",
        "| Scheme | Eb/N0 dB | BER | Channel uses / bit | Useful bits / channel use |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row.scheme} | {row.ebn0_db:.1f} | {row.ber:.6f} | {row.channel_uses_per_bit:.3f} | {row.useful_bits_per_channel_use:.6f} |"
        )
    (OUT_DIR / "comparison_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = run_comparison()
    save_results(rows)
    write_summary(rows)
    plot_ber(rows)
    plot_resource_efficiency(rows)
    print("Generated comparison outputs:")
    for path in sorted(OUT_DIR.glob("*.png")):
        print(f"  {path}")


if __name__ == "__main__":
    main()
