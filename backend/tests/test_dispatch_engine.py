from app.services.dispatch_engine import CallRequest, CarState, pick_car, plan_batch, score_car


def test_reject_when_full():
    car = CarState(1, 5, "idle", load=8, capacity=8)
    call = CallRequest(1, 5, "up", passengers=1)
    r = score_car(car, call)
    assert r.accepted is False
    assert "满员" in r.reason


def test_same_direction_beats_far_idle():
    cars = [
        CarState(1, 2, "up", load=1, capacity=10),
        CarState(2, 12, "idle", load=0, capacity=10),
    ]
    call = CallRequest(9, 4, "up", 1)
    best = pick_car(cars, call)
    assert best is not None
    assert best.car_id == 1


def test_closer_idle_wins_when_opposite():
    cars = [
        CarState(1, 10, "down", load=0, capacity=10),
        CarState(2, 3, "idle", load=0, capacity=10),
    ]
    call = CallRequest(3, 2, "up", 1)
    best = pick_car(cars, call)
    assert best is not None
    assert best.car_id == 2


def test_batch_accumulates_load_within_batch():
    # 两笔都能接：第一笔把 1 号轿厢塞满，第二笔必须改派 2 号轿厢
    cars = [
        CarState(1, 1, "idle", load=0, capacity=4),
        CarState(2, 10, "idle", load=0, capacity=4),
    ]
    calls = [
        CallRequest(11, 1, "up", passengers=4),
        CallRequest(12, 1, "up", passengers=1),
    ]
    result = plan_batch(cars, calls)
    assert result.failed_call_id is None
    assert [a.car_id for a in result.assignments] == [1, 2]
    # 纯函数：入参轿厢载荷不被修改
    assert cars[0].load == 0 and cars[1].load == 0


def test_batch_all_fails_when_second_overflows():
    # 1 号轿厢容量 2：第一笔 1 人可接（试派后载荷=2），第二笔 2 人必然满员；
    # 2 号轿厢初始已满。整批失败，第一笔也拿不到落库计划。
    cars = [
        CarState(1, 1, "idle", load=0, capacity=2),
        CarState(2, 3, "idle", load=5, capacity=5),
    ]
    calls = [
        CallRequest(21, 1, "up", passengers=1),
        CallRequest(22, 1, "up", passengers=2),
    ]
    result = plan_batch(cars, calls)
    assert result.failed_call_id == 22
    assert result.assignments == []
    # 入参维持提交前状态
    assert cars[0].load == 0 and cars[1].load == 5
