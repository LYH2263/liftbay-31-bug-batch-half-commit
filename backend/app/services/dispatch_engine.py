"""Elevator dispatch: same-direction preference + floor distance; reject if car full."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CarState:
    car_id: int
    floor: int
    direction: str  # "up" | "down" | "idle"
    load: int
    capacity: int


@dataclass(frozen=True)
class CallRequest:
    call_id: int
    floor: int
    direction: str  # desired travel after boarding
    passengers: int = 1


@dataclass(frozen=True)
class ScoreResult:
    car_id: int
    score: float
    accepted: bool
    reason: str


SAME_DIR_BONUS = 40.0
IDLE_BONUS = 20.0
DISTANCE_WEIGHT = 5.0


def score_car(car: CarState, call: CallRequest) -> ScoreResult:
    if car.load + call.passengers > car.capacity:
        return ScoreResult(car.car_id, -1e9, False, "轿厢满员")

    distance = abs(car.floor - call.floor)
    score = 100.0 - distance * DISTANCE_WEIGHT

    if car.direction == "idle":
        score += IDLE_BONUS
    elif car.direction == call.direction:
        # approaching or already going same way
        if car.direction == "up" and car.floor <= call.floor:
            score += SAME_DIR_BONUS
        elif car.direction == "down" and car.floor >= call.floor:
            score += SAME_DIR_BONUS
        else:
            score -= 15.0  # same dir but already passed
    else:
        score -= 25.0

    return ScoreResult(car.car_id, score, True, "ok")


@dataclass(frozen=True)
class Assignment:
    call_id: int
    car_id: int
    score: float


@dataclass(frozen=True)
class BatchPlanResult:
    assignments: list[Assignment]
    failed_call_id: int | None  # None 表示整批试派成功


def pick_car(cars: list[CarState], call: CallRequest) -> ScoreResult | None:
    results = [score_car(c, call) for c in cars]
    accepted = [r for r in results if r.accepted]
    if not accepted:
        return None
    return max(accepted, key=lambda r: r.score)


def plan_batch(cars: list[CarState], calls: list[CallRequest]) -> BatchPlanResult:
    """按 calls 给定顺序依次试派，并在同一批内累计轿厢载荷。

    每一笔都按当前（含本批已试派载荷）状态重新评分；任一笔找不到可接
    轿厢则整批失败，failed_call_id 指向该笔、assignments 为空。纯函数，
    不修改入参、不落库——由调用方在全部成功后一次性写入。
    """
    loads = {c.car_id: c.load for c in cars}
    plans: list[Assignment] = []
    for call in calls:
        current = [
            CarState(c.car_id, c.floor, c.direction, loads[c.car_id], c.capacity)
            for c in cars
        ]
        best = pick_car(current, call)
        if best is None:
            # 整批失败：丢弃此前累计的试派计划，调用方不得据此落库
            return BatchPlanResult([], call.call_id)
        loads[best.car_id] += call.passengers
        plans.append(Assignment(call.call_id, best.car_id, best.score))
    return BatchPlanResult(plans, None)


def congestion_by_floor(calls: list[CallRequest]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for c in calls:
        counts[c.floor] = counts.get(c.floor, 0) + c.passengers
    return counts
