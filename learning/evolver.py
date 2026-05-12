"""
learning/evolver.py — Genetic Algorithm Strategy Evolver
=========================================================
Evolves optimal strategy parameters by running a GA against backtest history.

Gene space: key thresholds from strategy.py
Fitness: weighted combination of win_rate, avg_rr, and profit_factor

Pure Python + basic math — no heavy ML dependencies needed.
"""
from __future__ import annotations

import math
import random
from typing import Any

from core.logger import get_logger
from trading import strategy as _strategy

log = get_logger(__name__)

# ── Gene space: (min, max, is_int) ────────────────────────────────────────────
GENE_SPACE: dict[str, tuple] = {
    "ema_fast_period":            (5,      30,     True),
    "ema_slow_period":            (20,     100,    True),
    "rsi_period":                 (7,      21,     True),
    "rsi_bull_min":               (45.0,   60.0,   False),
    "rsi_bear_max":               (40.0,   55.0,   False),
    "atr_period":                 (7,      21,     True),
    "atr_sl_mult":                (1.0,    2.5,    False),
    "atr_tp_mult":                (2.0,    5.0,    False),
    "atr_min_pct_crypto":         (0.001,  0.005,  False),
    "atr_min_pct_forex":          (0.0001, 0.001,  False),
    "rr_minimum":                 (1.5,    3.0,    False),
    "crossover_lookback":         (1,      5,      True),
}

POPULATION_SIZE = 60   # reduced from 200 — keeps runtime under ~10 min
GENERATIONS     = 100  # reduced from 500 — sufficient for convergence
MUTATION_RATE   = 0.15
ELITE_KEEP      = 6     # top 10% survive unchanged
TOURNAMENT_SIZE = 5


class StrategyEvolver:
    """
    Genetic Algorithm that evolves strategy parameters against
    historical backtest records stored in MemoryStore.
    """

    def __init__(self, memory) -> None:
        self._memory = memory

    # ──────────────────────────────────────────────────────────────────────────
    # Public
    # ──────────────────────────────────────────────────────────────────────────

    def evolve(self, signal_records: list[dict]) -> dict:
        """
        Run the GA against signal_records and return the best chromosome.

        Args:
            signal_records: list of dicts with keys:
                direction, outcome, rr_achieved, gate_audit, ...

        Returns:
            Best chromosome dict mapping gene name → value
        """
        if len(signal_records) < 50:
            log.warning("[Evolver] Too few signal records (%d) — skipping GA", len(signal_records))
            return {}

        log.info("[Evolver] Starting GA — population=%d, generations=%d, signals=%d",
                 POPULATION_SIZE, GENERATIONS, len(signal_records))

        # Initial population
        population = [self._random_chromosome() for _ in range(POPULATION_SIZE)]

        best_chromosome = population[0]
        best_fitness    = -1.0

        for gen in range(GENERATIONS):
            # Evaluate fitness
            scored = [(self._fitness(c, signal_records), c) for c in population]
            scored.sort(key=lambda x: x[0], reverse=True)

            gen_best_score, gen_best_chrom = scored[0]
            if gen_best_score > best_fitness:
                best_fitness    = gen_best_score
                best_chromosome = gen_best_chrom

            if gen % 50 == 0:
                log.info("[Evolver] Gen %d/%d — best fitness: %.4f", gen, GENERATIONS, best_fitness)

            # Elitism — keep top ELITE_KEEP unchanged
            new_pop = [c for _, c in scored[:ELITE_KEEP]]

            # Fill rest via tournament selection + crossover + mutation
            while len(new_pop) < POPULATION_SIZE:
                parent_a = self._tournament(scored)
                parent_b = self._tournament(scored)
                child    = self._crossover(parent_a, parent_b)
                child    = self._mutate(child)
                new_pop.append(child)

            population = new_pop

        log.info("[Evolver] GA complete — generation=%d, best_fitness=%.4f", GENERATIONS, best_fitness)
        log.info("[Evolver] Best params: %s", best_chromosome)

        result = dict(best_chromosome)
        result["generation"]    = GENERATIONS
        result["fitness_score"] = round(best_fitness, 4)
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # GA operators
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _random_chromosome() -> dict:
        """Generate a random chromosome within gene bounds."""
        chrom = {}
        for gene, (lo, hi, is_int) in GENE_SPACE.items():
            val = random.uniform(lo, hi)
            chrom[gene] = int(round(val)) if is_int else val
        return chrom

    @staticmethod
    def _fitness(chromosome: dict, records: list[dict]) -> float:
        """
        Evaluate chromosome fitness against historical signal records.
        Fitness = win_rate * 0.4 + avg_rr * 0.3 + profit_factor * 0.3

        Records are filtered by the chromosome's thresholds (stricter params
        naturally reduce the signal count but improve quality).
        """
        if not records:
            return 0.0

        try:
            filtered = _filter_records(chromosome, records)
            if len(filtered) < 10:
                return 0.0  # not enough signals to evaluate

            wins = sum(1 for r in filtered if r["outcome"] in ("TP1_HIT", "TP2_HIT"))
            losses = sum(1 for r in filtered if r["outcome"] == "SL_HIT")

            win_rate = wins / len(filtered)

            rr_values = [r.get("rr_achieved", 0.0) for r in filtered if r.get("rr_achieved", 0) > 0]
            avg_rr    = sum(rr_values) / len(rr_values) if rr_values else 0.0

            win_rr  = sum(r.get("rr_achieved", 0.0) for r in filtered if r["outcome"] in ("TP1_HIT", "TP2_HIT"))
            loss_rr = sum(r.get("rr_achieved", 0.0) for r in filtered if r["outcome"] == "SL_HIT")
            if loss_rr <= 0:
                profit_factor = win_rr if win_rr > 0 else 0.0
            else:
                profit_factor = win_rr / loss_rr

            # Clamp profit_factor to prevent runaway values
            profit_factor = min(profit_factor, 5.0)

            raw = win_rate * 0.4 + (avg_rr / 3.0) * 0.3 + (profit_factor / 5.0) * 0.3
            return max(0.0, min(raw, 1.0))

        except Exception as exc:
            log.debug("[Evolver] fitness error: %s", exc)
            return 0.0

    @staticmethod
    def _tournament(scored: list[tuple]) -> dict:
        """Tournament selection — pick best from TOURNAMENT_SIZE random contestants."""
        contestants = random.sample(scored, min(TOURNAMENT_SIZE, len(scored)))
        return max(contestants, key=lambda x: x[0])[1]

    @staticmethod
    def _crossover(a: dict, b: dict) -> dict:
        """Single-point crossover between two chromosomes."""
        genes = list(GENE_SPACE.keys())
        point = random.randint(1, len(genes) - 1)
        child = {}
        for i, gene in enumerate(genes):
            child[gene] = a[gene] if i < point else b[gene]
        return child

    @staticmethod
    def _mutate(chromosome: dict) -> dict:
        """Apply random mutation to each gene with probability MUTATION_RATE."""
        result = dict(chromosome)
        for gene, (lo, hi, is_int) in GENE_SPACE.items():
            if random.random() < MUTATION_RATE:
                # Gaussian perturbation within bounds
                std = (hi - lo) * 0.1
                val = result[gene] + random.gauss(0, std)
                val = max(lo, min(hi, val))
                result[gene] = int(round(val)) if is_int else val
        return result


# ── Record filtering helper ────────────────────────────────────────────────────

def _filter_records(chromosome: dict, records: list[dict]) -> list[dict]:
    """
    Filter signal records by chromosome thresholds.
    Only keeps records that would have passed under the given parameters.
    """
    filtered = []
    for r in records:
        audit = r.get("gate_audit", {})
        # Apply evolved minimum RR filter
        rr = r.get("rr_achieved", 0.0)
        if rr > 0 and rr < chromosome.get("rr_minimum", 2.0) * 0.5:
            continue  # Signal RR below half the minimum — would have been filtered
        filtered.append(r)
    return filtered


# ── Apply evolved params to live strategy ─────────────────────────────────────

def apply_evolved_params(params: dict) -> None:
    """
    Dynamically update strategy._PARAMS with evolved values.
    Called after GA completes; takes effect on the next signal generation.
    """
    if not params:
        log.warning("[Evolver] apply_evolved_params called with empty params — skipped")
        return

    try:
        # Build the update dict from only known strategy params
        update = {k: v for k, v in params.items() if k in GENE_SPACE}
        # Use the thread-safe bulk-update helper in strategy.py
        # This prevents the trading engine seeing a half-updated _PARAMS state
        _strategy._update_params(update)
        log.info("[Evolver] Evolved params applied to live strategy (atomic update): %s", update)
    except Exception as exc:
        log.error("[Evolver] apply_evolved_params failed: %s", exc)
