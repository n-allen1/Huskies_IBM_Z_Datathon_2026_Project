"""The multi-incident allocation solver, tested on its own against a brute-force oracle.

The solver works on plain data (`Problem`/`Provider`), so it is checked exhaustively: for random small instances every possible
assignment of units to incidents is enumerated and the solver's plan must reach the best lexicographic score.
SciPy-dependent tests are skipped on an install without SciPy (the fallback is then the thing under test)."""

import itertools
import random
from uuid import UUID

import pytest

from app import multi_incident as mi
from app.multi_incident import Need, Plan, Problem, Provider, objective_vector, optimize, solve_greedy


def uid(n: int) -> UUID:
    return UUID(int=n)


def provider(n: int, sign: str, eta: int, *caps: str) -> Provider:
    return Provider(uid(n), sign, eta, frozenset(caps))


def problem(n: int, label: str, needs: list[tuple[str, int]], providers: list[Provider]) -> Problem:
    return Problem(uid(1000 + n), label, tuple(Need(c, q) for c, q in needs), tuple(providers))


def brute_force_best(problems: list[Problem]) -> tuple:
    """Try every way of giving each unit to one incident (or none) and keep the best lexicographic score."""
    units = sorted({pr.unit_id for p in problems for pr in p.providers})
    options = {u: [None] + [p.incident_id for p in problems if any(pr.unit_id == u for pr in p.providers)] for u in units}
    best = None
    for choice in itertools.product(*(options[u] for u in units)):
        assignment: dict[UUID, list[UUID]] = {p.incident_id: [] for p in problems}
        for u, target in zip(units, choice):
            if target is not None:
                assignment[target].append(u)
        score = objective_vector(problems, {k: tuple(v) for k, v in assignment.items()})
        best = score if best is None or score > best else best
    return best


def assert_feasible(problems: list[Problem], plan: Plan) -> None:
    seen: set[UUID] = set()
    for p in problems:
        allowed = {pr.unit_id for pr in p.providers}
        for u in plan.assignment[p.incident_id]:
            assert u in allowed, "a unit was sent to an incident it is not eligible for"
            assert u not in seen, "a unit was allocated to two incidents"
            seen.add(u)


def random_instance(rng: random.Random) -> list[Problem]:
    caps = ["A", "B", "C"]
    n_units = rng.randint(1, 6)
    n_incidents = rng.randint(1, 3)
    problems = []
    for i in range(n_incidents):
        needs = [(c, rng.randint(1, 2)) for c in rng.sample(caps, rng.randint(1, 3))]
        wanted = {c for c, _ in needs}
        providers = []
        for u in range(n_units):
            if rng.random() < 0.75:  # eligible for this incident
                provides = {c for c in wanted if rng.random() < 0.6}
                if provides:
                    providers.append(provider(u, f"U{u}", rng.randint(0, 900), *provides))
        problems.append(problem(i, f"INC-{i}", needs, providers))
    return problems


@pytest.mark.parametrize("seed", range(150))
def test_exact_solver_matches_brute_force(seed):
    pytest.importorskip("scipy")
    problems = random_instance(random.Random(seed))
    plan = optimize(problems)
    assert plan.optimizer == mi.OPTIMIZER_MILP
    assert_feasible(problems, plan)
    assert objective_vector(problems, plan.assignment) == brute_force_best(problems)


@pytest.mark.parametrize("seed", range(150))
def test_greedy_fallback_is_always_feasible_and_never_better_than_optimal(seed):
    problems = random_instance(random.Random(seed))
    plan = Plan(mi.OPTIMIZER_FALLBACK, solve_greedy(problems))
    assert_feasible(problems, plan)
    assert objective_vector(problems, plan.assignment) <= brute_force_best(problems)


def test_the_exact_solver_staffs_both_incidents_where_greedy_cannot():
    """u1 can fill A and B but is the fastest for A; u2 can only fill A. Greedy gives u1 to the first incident
    and strands the second; the joint optimum gives the first incident u2 and the second incident u1."""
    pytest.importorskip("scipy")
    u1 = provider(1, "U1", 100, "A", "B")
    u2 = provider(2, "U2", 200, "A")
    first = problem(1, "INC-1", [("A", 1)], [u1, u2])
    second = problem(2, "INC-2", [("A", 1), ("B", 1)], [u1])
    exact = optimize([first, second])
    assert exact.assignment[first.incident_id] == (uid(2),)
    assert exact.assignment[second.incident_id] == (uid(1),)
    greedy = solve_greedy([first, second])
    assert greedy[first.incident_id] == (uid(1),) and greedy[second.incident_id] == ()


def test_the_higher_priority_incident_gets_the_only_unit_that_can_serve_both():
    pytest.importorskip("scipy")
    only = provider(1, "HR4", 300, "H")
    high = problem(1, "INC-HIGH", [("H", 1)], [only])
    low = problem(2, "INC-LOW", [("H", 1)], [only])
    assert optimize([high, low]).assignment == {high.incident_id: (uid(1),), low.incident_id: ()}
    assert optimize([low, high]).assignment == {low.incident_id: (uid(1),), high.incident_id: ()}  # order IS the priority


def test_a_shortage_falls_on_the_lowest_priority_incident():
    """Three units, a needs two and b needs two: the high-priority incident is fully staffed and the other gets what is left."""
    pytest.importorskip("scipy")
    units = [provider(n, f"U{n}", 100 * n, "P") for n in (1, 2, 3)]
    high = problem(1, "INC-HIGH", [("P", 2)], units)
    low = problem(2, "INC-LOW", [("P", 2)], units)
    plan = optimize([high, low])
    assert len(plan.assignment[high.incident_id]) == 2
    assert len(plan.assignment[low.incident_id]) == 1


def test_no_unit_is_sent_that_adds_nothing():
    pytest.importorskip("scipy")
    near = provider(1, "NEAR", 0, "A")  # already at the scene, but not needed beyond the first
    other = provider(2, "OTHER", 0, "A")
    p = problem(1, "INC", [("A", 1)], [near, other])
    assert len(optimize([p]).assignment[p.incident_id]) == 1


def test_a_single_incident_matches_the_plain_package():
    pytest.importorskip("scipy")
    units = [provider(1, "E1", 120, "FIRE"), provider(2, "E2", 240, "FIRE"), provider(3, "L1", 300, "LADDER", "FIRE")]
    p = problem(1, "INC", [("FIRE", 2), ("LADDER", 1)], units)
    plan = optimize([p])
    chosen = plan.assignment[p.incident_id]
    assert mi.is_staffed(p, set(chosen))
    assert objective_vector([p], plan.assignment) == brute_force_best([p])


def test_the_plan_is_deterministic():
    pytest.importorskip("scipy")
    problems = random_instance(random.Random(7))
    assert optimize(problems).assignment == optimize(problems).assignment


def test_an_unavailable_solver_degrades_to_the_labeled_fallback(monkeypatch):
    def boom(_problems):
        raise ImportError("no scipy here")

    monkeypatch.setattr(mi, "solve_milp", boom)
    p = problem(1, "INC", [("A", 1)], [provider(1, "U1", 60, "A")])
    plan = optimize([p])
    assert plan.optimizer == mi.OPTIMIZER_FALLBACK
    assert plan.assignment[p.incident_id] == (uid(1),)
    assert any("greedy" in n for n in plan.notes)


def test_a_failing_solver_degrades_instead_of_raising(monkeypatch):
    def boom(_problems):
        raise RuntimeError("solver status 4")

    monkeypatch.setattr(mi, "solve_milp", boom)
    p = problem(1, "INC", [("A", 1)], [provider(1, "U1", 60, "A")])
    plan = optimize([p])
    assert plan.optimizer == mi.OPTIMIZER_FALLBACK and plan.notes
