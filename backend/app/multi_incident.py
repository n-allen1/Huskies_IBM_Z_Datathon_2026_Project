"""Joint allocation of units across several simultaneous incidents (contract stage `multi_incident_optimization`).

This module only decides *who goes where among units that are already hard-eligible*. Eligibility, the protocol's requirements and
the ETAs are computed per incident by `eligibility.evaluate` before anything gets here, and a unit appears in an incident's
`providers` only if it passed hard eligibility for that incident. The optimizer can therefore never send an ineligible unit
anywhere; it can only choose between eligible ones. It does not know what a person, credential or capability is.

The objective is lexicographic, strictly in this order:
  1. Fully staff the highest-priority incident, then the next one, and so on (an incident is "staffed" when every mandatory
     requirement has its quantity covered).
  2. Cover as many individual mandatory requirement slots as possible, highest priority first, so a shortage is spread
     as little as possible and falls on the lowest-priority incident.
  3. Among those, minimize the total estimated arrival time of the units used (each used unit also costs one second, so a unit
     that adds nothing is not sent).
Incidents must be passed in priority order (most important first); the caller decides what priority means.

Solving: exact mixed-integer programming with `scipy.optimize.milp` (HiGHS), one small solve per objective level. SciPy ships in
the IBM LinuxONE image. Without SciPy (or if the solver fails) it falls back to a greedy allocation in priority order, and the plan
says so; that result is feasible but not guaranteed optimal. Everything is deterministic: identical inputs give identical plans.
"""

from dataclasses import dataclass, field
from typing import Sequence
from uuid import UUID

OPTIMIZER_MILP = "scipy-highs-milp-lexicographic"
OPTIMIZER_FALLBACK = "priority-greedy-fallback"
# Every unit used costs its ETA plus one second, so a unit that adds nothing is never sent just because it is already close.
_UNIT_COST = 1


@dataclass(frozen=True)
class Need:
    capability_id: str
    quantity: int


@dataclass(frozen=True)
class Provider:
    """A hard-eligible unit for one incident, and which of that incident's needs it can fill."""

    unit_id: UUID
    call_sign: str
    eta_seconds: int
    provides: frozenset[str]


@dataclass(frozen=True)
class Problem:
    incident_id: UUID
    label: str
    needs: tuple[Need, ...]
    providers: tuple[Provider, ...]


@dataclass
class Plan:
    optimizer: str
    assignment: dict[UUID, tuple[UUID, ...]]  # incident id -> unit ids (sorted by call sign)
    notes: list[str] = field(default_factory=list)


def _covered(problem: Problem, unit_ids: set[UUID]) -> dict[str, int]:
    """Per need: how many of its required slots the chosen units fill (capped at the quantity)."""
    return {
        n.capability_id: min(n.quantity, sum(1 for p in problem.providers if p.unit_id in unit_ids and n.capability_id in p.provides))
        for n in problem.needs
    }


def is_staffed(problem: Problem, unit_ids: set[UUID]) -> bool:
    covered = _covered(problem, unit_ids)
    return all(covered[n.capability_id] >= n.quantity for n in problem.needs)


def objective_vector(problems: Sequence[Problem], assignment: dict[UUID, tuple[UUID, ...]]) -> tuple:
    """The lexicographic score of an allocation (bigger is better); used to compare plans in tests."""
    chosen = {p.incident_id: set(assignment.get(p.incident_id, ())) for p in problems}
    staffed = tuple(int(is_staffed(p, chosen[p.incident_id])) for p in problems)
    slots = tuple(sum(_covered(p, chosen[p.incident_id]).values()) for p in problems)
    eta = {(p.incident_id, pr.unit_id): pr.eta_seconds for p in problems for pr in p.providers}
    total_cost = sum(eta[(iid, u)] + _UNIT_COST for iid, units in assignment.items() for u in units)
    return (*staffed, *slots, -total_cost)


def _by_sign(problem: Problem, unit_ids: set[UUID]) -> tuple[UUID, ...]:
    signs = {p.unit_id: p.call_sign for p in problem.providers}
    return tuple(sorted(unit_ids, key=lambda u: (signs[u], str(u))))


# --- exact: lexicographic MILP --------------------------------------------------

def solve_milp(problems: Sequence[Problem]) -> dict[UUID, tuple[UUID, ...]]:
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp

    ordered = [sorted(p.providers, key=lambda pr: (pr.call_sign, str(pr.unit_id))) for p in problems]
    # Columns: x[i][j] (provider j of incident i is used), then c[i][k] (slots filled for need k), then s[i] (incident staffed).
    col = 0
    x_col: dict[tuple[int, int], int] = {}
    for i, provs in enumerate(ordered):
        for j in range(len(provs)):
            x_col[(i, j)] = col
            col += 1
    c_col: dict[tuple[int, int], int] = {}
    for i, p in enumerate(problems):
        for k in range(len(p.needs)):
            c_col[(i, k)] = col
            col += 1
    s_col = {i: col + i for i in range(len(problems))}
    n_vars = col + len(problems)

    lower, upper, integrality = np.zeros(n_vars), np.ones(n_vars), np.ones(n_vars)
    for (i, k), idx in c_col.items():
        upper[idx] = problems[i].needs[k].quantity

    rows: list[tuple[np.ndarray, float, float]] = []

    def row() -> np.ndarray:
        return np.zeros(n_vars)

    # A unit serves at most one incident.
    units = sorted({pr.unit_id for provs in ordered for pr in provs}, key=str)
    for u in units:
        r = row()
        for i, provs in enumerate(ordered):
            for j, pr in enumerate(provs):
                if pr.unit_id == u:
                    r[x_col[(i, j)]] = 1
        rows.append((r, 0, 1))
    for i, p in enumerate(problems):
        for k, need in enumerate(p.needs):
            # slots filled cannot exceed the chosen units that provide the need ...
            r = row()
            r[c_col[(i, k)]] = 1
            for j, pr in enumerate(ordered[i]):
                if need.capability_id in pr.provides:
                    r[x_col[(i, j)]] = -1
            rows.append((r, -np.inf, 0))
            # ... and an incident counts as staffed only if every need is fully filled.
            r = row()
            r[s_col[i]] = need.quantity
            r[c_col[(i, k)]] = -1
            rows.append((r, -np.inf, 0))

    cap_obj = [np.zeros(n_vars) for _ in problems]
    for i, p in enumerate(problems):
        for k in range(len(p.needs)):
            cap_obj[i][c_col[(i, k)]] = 1
    eta_obj = np.zeros(n_vars)
    for (i, j), idx in x_col.items():
        eta_obj[idx] = ordered[i][j].eta_seconds + _UNIT_COST

    stages: list[tuple[np.ndarray, bool]] = []  # (objective to maximize, integer-valued)
    for i in range(len(problems)):
        v = np.zeros(n_vars)
        v[s_col[i]] = 1
        stages.append((v, True))
    stages += [(cap_obj[i], True) for i in range(len(problems))]
    stages.append((-eta_obj, False))

    fixed: list[tuple[np.ndarray, float, float]] = []
    z = None
    for objective, integer_valued in stages:
        constraints = [LinearConstraint(np.array([r for r, _, _ in rows + fixed]), np.array([lo for _, lo, _ in rows + fixed]), np.array([hi for _, _, hi in rows + fixed]))]
        res = milp(-objective, constraints=constraints, integrality=integrality, bounds=Bounds(lower, upper))
        if res.status != 0 or res.x is None:
            raise RuntimeError(f"MILP stage failed: status {res.status} ({res.message})")
        z = res.x
        best = float(objective @ z)
        # Hold this level at its optimum while later levels are optimized. Integer objectives get a half-unit slack.
        slack = 0.5 if integer_valued else 1e-6 * max(1.0, abs(best))
        fixed.append((objective.copy(), best - slack, np.inf))

    assert z is not None
    result: dict[UUID, tuple[UUID, ...]] = {}
    for i, p in enumerate(problems):
        used = {ordered[i][j].unit_id for j in range(len(ordered[i])) if z[x_col[(i, j)]] > 0.5}
        result[p.incident_id] = _by_sign(p, used)
    return result


# --- fallback: greedy in priority order -------------------------------------------

def solve_greedy(problems: Sequence[Problem]) -> dict[UUID, tuple[UUID, ...]]:
    taken: set[UUID] = set()
    result: dict[UUID, tuple[UUID, ...]] = {}
    for p in problems:
        pool = sorted((pr for pr in p.providers if pr.unit_id not in taken), key=lambda pr: (pr.eta_seconds, pr.call_sign, str(pr.unit_id)))
        providers = {n.capability_id: [pr for pr in pool if n.capability_id in pr.provides] for n in p.needs}
        chosen: list[Provider] = []
        for n in sorted(p.needs, key=lambda n: (len(providers[n.capability_id]), n.capability_id)):
            have = sum(n.capability_id in pr.provides for pr in chosen)
            for pr in providers[n.capability_id]:
                if have >= n.quantity:
                    break
                if pr not in chosen:
                    chosen.append(pr)
                    have += 1
        ids = {pr.unit_id for pr in chosen}
        taken |= ids
        result[p.incident_id] = _by_sign(p, ids)
    return result


def optimize(problems: Sequence[Problem]) -> Plan:
    """Allocate units across the incidents (given most important first). Never raises on solver trouble: it falls back."""
    notes: list[str] = []
    try:
        return Plan(OPTIMIZER_MILP, solve_milp(problems), notes)
    except ImportError:
        notes.append("SciPy is not installed here, so the allocation is greedy in priority order and may not be optimal.")
    except Exception as exc:  # noqa: BLE001 - any solver failure must degrade, not take the endpoint down
        notes.append(f"The exact solver failed ({type(exc).__name__}), so the allocation is greedy in priority order and may not be optimal.")
    return Plan(OPTIMIZER_FALLBACK, solve_greedy(problems), notes)
