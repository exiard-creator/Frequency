"""Generate GNSS/GPS-like phase tracking stress comparison figures.

This is a public-facing explanatory model, not a GPS L1 C/A receiver simulator.
It compares simple known GNSS receiver observables with a phase-trace fallback
style observable under weak-signal and high-dynamics stress.

Run from the workspace root:
    python Frequency/Github/src/gps_phase_tracking_comparison.py

Outputs:
    Frequency/Github/outputs/gps_phase_rmse_comparison.png
    Frequency/Github/outputs/gps_outage_comparison.png
    Frequency/Github/outputs/gps_phase_tracking_results.json
    Frequency/Github/outputs/gps_phase_tracking_summary.md
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import math

import matplotlib.pyplot as plt
import numpy as np


OUT_DIR = Path("Frequency/Github/outputs")
CN0_DBHZ = np.array([18.0, 20.0, 22.0, 24.0, 26.0, 28.0, 30.0, 32.0, 34.0, 36.0, 38.0, 40.0, 42.0, 45.0])
DEFAULT_EPOCHS = 2_000
DEFAULT_SEED = 2026070602
COHERENT_TIME_S = 0.02
L1_WAVELENGTH_M = 0.190293672798365
C_M_PER_S = 299_792_458.0
CA_CHIP_RATE_HZ = 1.023e6


@dataclass(frozen=True)
class GpsSchemeResult:
    scheme: str
    cn0_dbhz: float
    phase_rmse_rad: float
    range_rmse_m: float
    outage_probability: float
    resource_units_per_epoch: float
    note: str


def cn0_to_linear(cn0_dbhz: float) -> float:
    return 10.0 ** (cn0_dbhz / 10.0)


def wrap_angle(angle: np.ndarray | float) -> np.ndarray | float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def generate_true_phase(epochs: int, rng: np.random.Generator) -> np.ndarray:
    """Create a GNSS-like carrier phase trajectory with oscillator and dynamics."""
    t = np.arange(epochs)
    doppler = 0.018 * t
    jerk = 0.0000025 * t**2
    oscillator = np.cumsum(0.015 * rng.normal(size=epochs))
    vibration = 0.20 * np.sin(2.0 * math.pi * t / 137.0)
    return wrap_angle(doppler + jerk + oscillator + vibration)


def thermal_phase_sigma(cn0_dbhz: float, coherent_time_s: float = COHERENT_TIME_S) -> float:
    """Approximate phase-noise sigma from C/N0 and coherent integration time."""
    cn0 = cn0_to_linear(cn0_dbhz)
    snr = max(cn0 * coherent_time_s, 1e-12)
    return math.sqrt(1.0 / (2.0 * snr))


def simulate_code_tracking(
    true_phase: np.ndarray,
    cn0_dbhz: float,
    rng: np.random.Generator,
) -> GpsSchemeResult:
    """Coarse pseudorange/code tracking reference.

    Code tracking is robust and absolute, but its phase-equivalent precision is
    much worse than carrier phase.  The model uses a conservative chip-fraction
    noise approximation for illustration.
    """
    cn0 = cn0_to_linear(cn0_dbhz)
    code_sigma_m = C_M_PER_S / CA_CHIP_RATE_HZ / max(math.sqrt(cn0 * COHERENT_TIME_S), 1e-12)
    code_sigma_m = min(code_sigma_m, 1_000.0)
    phase_equiv = (2.0 * math.pi / L1_WAVELENGTH_M) * code_sigma_m
    outage = float(np.clip(0.55 - 0.045 * (cn0_dbhz - 18.0), 0.01, 0.80))
    return GpsSchemeResult(
        scheme="legacy_code_tracking",
        cn0_dbhz=float(cn0_dbhz),
        phase_rmse_rad=float(phase_equiv),
        range_rmse_m=float(code_sigma_m),
        outage_probability=outage,
        resource_units_per_epoch=1.0,
        note="Robust absolute code observable; coarse range precision in weak signal.",
    )


def simulate_carrier_pll(
    true_phase: np.ndarray,
    cn0_dbhz: float,
    rng: np.random.Generator,
) -> GpsSchemeResult:
    """Carrier tracking loop reference with cycle-slip/outage stress."""
    sigma = thermal_phase_sigma(cn0_dbhz)
    dynamic_sigma = max(0.55 - 0.018 * cn0_dbhz, 0.02)
    innovation = sigma * rng.normal(size=true_phase.size) + dynamic_sigma * rng.normal(size=true_phase.size)
    measured = wrap_angle(true_phase + innovation)
    phase_error = np.asarray(wrap_angle(measured - true_phase))
    slip = np.abs(phase_error) > 1.05
    outage = float(np.mean(slip))
    kept = phase_error[~slip]
    rmse = float(np.sqrt(np.mean(kept**2))) if kept.size else math.pi
    return GpsSchemeResult(
        scheme="carrier_pll_tracking",
        cn0_dbhz=float(cn0_dbhz),
        phase_rmse_rad=rmse,
        range_rmse_m=float(rmse * L1_WAVELENGTH_M / (2.0 * math.pi)),
        outage_probability=outage,
        resource_units_per_epoch=1.0,
        note="Fine carrier observable, but can suffer loss-of-lock/cycle slips under weak signal or dynamics.",
    )


def simulate_differential_carrier(
    true_phase: np.ndarray,
    cn0_dbhz: float,
    rng: np.random.Generator,
) -> GpsSchemeResult:
    """Adjacent-epoch differential carrier phase reference."""
    sigma = thermal_phase_sigma(cn0_dbhz)
    measured = wrap_angle(true_phase + sigma * rng.normal(size=true_phase.size))
    diff_true = wrap_angle(true_phase[1:] - true_phase[:-1])
    diff_meas = wrap_angle(measured[1:] - measured[:-1])
    err = np.asarray(wrap_angle(diff_meas - diff_true))
    outage = float(np.mean(np.abs(err) > 1.15))
    kept = err[np.abs(err) <= 1.15]
    rmse = float(np.sqrt(np.mean(kept**2))) if kept.size else math.pi
    return GpsSchemeResult(
        scheme="differential_carrier_phase",
        cn0_dbhz=float(cn0_dbhz),
        phase_rmse_rad=rmse,
        range_rmse_m=float(rmse * L1_WAVELENGTH_M / (2.0 * math.pi)),
        outage_probability=outage,
        resource_units_per_epoch=2.0,
        note="Avoids absolute phase, but adjacent differencing doubles independent thermal noise.",
    )


def simulate_phase_trace_fallback(
    true_phase: np.ndarray,
    cn0_dbhz: float,
    rng: np.random.Generator,
    pairs: int = 4,
    repetitions: int = 3,
) -> GpsSchemeResult:
    """GNSS-like phase-trace fallback observable.

    This is not a legacy GPS waveform.  It represents a future/auxiliary
    multitone or multi-correlator aid channel where common oscillator phase is
    suppressed by conjugate products and vector combining.
    """
    sigma = thermal_phase_sigma(cn0_dbhz)
    errors = []
    slips = []
    static_pair_bias = rng.uniform(-0.8, 0.8, size=pairs)
    pair_weight = np.linspace(1.0, 0.65, pairs)
    for phase in true_phase:
        vectors = []
        weights = []
        for rep in range(repetitions):
            common = phase + rng.uniform(-math.pi, math.pi)
            for pair in range(pairs):
                n1 = sigma * (rng.normal() + 1j * rng.normal()) / math.sqrt(2.0)
                n2 = sigma * (rng.normal() + 1j * rng.normal()) / math.sqrt(2.0)
                left = np.exp(1j * (phase + common + static_pair_bias[pair])) + n1
                right = np.exp(1j * (common + static_pair_bias[pair])) + n2
                trace = left * np.conj(right)
                corrected = trace * np.exp(-1j * 0.0)
                vectors.append(corrected)
                weights.append(pair_weight[pair])
        combined = sum(w * v for w, v in zip(weights, vectors))
        estimate = float(np.angle(combined))
        err = float(wrap_angle(estimate - phase))
        errors.append(err)
        slips.append(abs(err) > 1.15)
    errors_arr = np.array(errors)
    slips_arr = np.array(slips)
    kept = errors_arr[~slips_arr]
    rmse = float(np.sqrt(np.mean(kept**2))) if kept.size else math.pi
    return GpsSchemeResult(
        scheme="phase_trace_fallback_aid",
        cn0_dbhz=float(cn0_dbhz),
        phase_rmse_rad=rmse,
        range_rmse_m=float(rmse * L1_WAVELENGTH_M / (2.0 * math.pi)),
        outage_probability=float(np.mean(slips_arr)),
        resource_units_per_epoch=float(2 * pairs * repetitions),
        note="Auxiliary/future multitone aid channel; robust by pair/repetition vector combining, resource-expensive.",
    )


def run_comparison(epochs: int = DEFAULT_EPOCHS, seed: int = DEFAULT_SEED) -> list[GpsSchemeResult]:
    master = np.random.default_rng(seed)
    rows: list[GpsSchemeResult] = []
    for cn0 in CN0_DBHZ:
        phase_rng = np.random.default_rng(int(master.integers(0, 2**31 - 1)))
        true_phase = generate_true_phase(epochs, phase_rng)
        for sim in [
            simulate_code_tracking,
            simulate_carrier_pll,
            simulate_differential_carrier,
            simulate_phase_trace_fallback,
        ]:
            rng = np.random.default_rng(int(master.integers(0, 2**31 - 1)))
            rows.append(sim(true_phase, float(cn0), rng))
    return rows


def plot_phase_rmse(rows: list[GpsSchemeResult]) -> None:
    plt.figure(figsize=(8.2, 5.2))
    phase_schemes = {"carrier_pll_tracking", "differential_carrier_phase", "phase_trace_fallback_aid"}
    for scheme in sorted(phase_schemes):
        subset = [row for row in rows if row.scheme == scheme]
        xs = [row.cn0_dbhz for row in subset]
        ys = [row.phase_rmse_rad for row in subset]
        plt.semilogy(xs, ys, marker="o", label=scheme)
    plt.xlabel("C/N0 (dB-Hz)")
    plt.ylabel("Phase RMSE (rad)")
    plt.title("GNSS-like carrier-phase tracking stress comparison")
    plt.grid(True, which="both", alpha=0.35)
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.figtext(0.5, 0.01, "Legacy code tracking is omitted here because chip-level code range maps to much larger phase-equivalent radians; see JSON/summary.", ha="center", fontsize=8)
    plt.savefig(OUT_DIR / "gps_phase_rmse_comparison.png", dpi=180)
    plt.close()


def plot_outage(rows: list[GpsSchemeResult]) -> None:
    plt.figure(figsize=(8.2, 5.2))
    for scheme in sorted({row.scheme for row in rows}):
        subset = [row for row in rows if row.scheme == scheme]
        xs = [row.cn0_dbhz for row in subset]
        ys = [row.outage_probability for row in subset]
        plt.semilogy(xs, [max(y, 0.5 / DEFAULT_EPOCHS) for y in ys], marker="o", label=scheme)
    plt.xlabel("C/N0 (dB-Hz)")
    plt.ylabel("Outage / slip probability (floor shown)")
    plt.title("Weak-signal tracking outage comparison")
    plt.grid(True, which="both", alpha=0.35)
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "gps_outage_comparison.png", dpi=180)
    plt.close()


def save_results(rows: list[GpsSchemeResult]) -> None:
    payload = {
        "metadata": {
            "epochs": DEFAULT_EPOCHS,
            "seed": DEFAULT_SEED,
            "cn0_dbhz": CN0_DBHZ.tolist(),
            "important_note": "This is a GNSS-like stress model, not a standard GPS L1 C/A receiver simulator.",
        },
        "results": [asdict(row) for row in rows],
    }
    (OUT_DIR / "gps_phase_tracking_results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_summary(rows: list[GpsSchemeResult]) -> None:
    lines = [
        "# GPS/GNSS-like Phase Tracking Summary",
        "",
        "This file is generated by `src/gps_phase_tracking_comparison.py`.",
        "",
        "## Important Interpretation",
        "",
        "This is not a claim that the phase-trace method can be inserted directly into legacy GPS L1 C/A without waveform changes.",
        "The phase-trace row should be read as a future or auxiliary multitone/multi-correlator fallback aid channel for weak-signal phase continuity.",
        "",
        "## Results",
        "",
        "| Scheme | C/N0 dB-Hz | Phase RMSE rad | Range equiv. RMSE m | Outage probability | Resource units / epoch |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row.scheme} | {row.cn0_dbhz:.1f} | {row.phase_rmse_rad:.6f} | {row.range_rmse_m:.6f} | {row.outage_probability:.6f} | {row.resource_units_per_epoch:.1f} |"
        )
    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- `legacy_code_tracking` is robust but coarse; it is not intended to compete with carrier phase precision.",
            "- `carrier_pll_tracking` is precise when locked, but the model includes weak-signal/high-dynamics slip risk.",
            "- `phase_trace_fallback_aid` is not a legacy GPS waveform.  It represents a possible auxiliary/future signal structure or receiver-side multi-correlator aid.",
            "- Range-equivalent RMSE is computed from L1 carrier wavelength only for scale comparison; full positioning error needs satellite geometry, clocks, ionosphere, troposphere, ephemeris, and multipath models.",
        ]
    )
    (OUT_DIR / "gps_phase_tracking_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = run_comparison()
    save_results(rows)
    write_summary(rows)
    plot_phase_rmse(rows)
    plot_outage(rows)
    print("Generated GPS/GNSS-like comparison outputs:")
    for path in sorted(OUT_DIR.glob("gps_*")):
        print(f"  {path}")


if __name__ == "__main__":
    main()