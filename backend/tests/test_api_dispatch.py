from fastapi import status

from app.models.models import Building, CallTicket, DispatchLog, ElevatorCar


def _make_world(db):
    b = Building(name="测试楼", floors=10)
    db.add(b)
    db.flush()
    # 1 号轿厢仅余 2 人空间；2 号轿厢已满
    car1 = ElevatorCar(building_id=b.id, label="T1", floor=1, direction="idle", load=0, capacity=2)
    car2 = ElevatorCar(building_id=b.id, label="T2", floor=3, direction="idle", load=5, capacity=5)
    db.add_all([car1, car2])
    c1 = CallTicket(building_id=b.id, floor=1, direction="up", passengers=1, status="waiting")
    c2 = CallTicket(building_id=b.id, floor=1, direction="up", passengers=2, status="waiting")
    db.add_all([c1, c2])
    db.commit()
    return b, car1, car2, c1, c2


def test_batch_rolls_back_when_second_call_overflows(client, db_session):
    b, car1, car2, c1, c2 = _make_world(db_session)
    loads_before = (db_session.get(ElevatorCar, car1.id).load,
                    db_session.get(ElevatorCar, car2.id).load)
    logs_before = db_session.query(DispatchLog).count()

    resp = client.post("/api/dispatch/batch", json={"call_ids": [c1.id, c2.id]})

    assert resp.status_code == status.HTTP_409_CONFLICT
    assert str(c2.id) in resp.json()["detail"]

    db_session.expire_all()
    # 两笔呼梯全部仍 waiting
    t1 = db_session.get(CallTicket, c1.id)
    t2 = db_session.get(CallTicket, c2.id)
    assert t1.status == "waiting" and t1.assigned_car_id is None and t1.score == ""
    assert t2.status == "waiting" and t2.assigned_car_id is None
    # 轿厢载荷恢复提交前
    assert db_session.get(ElevatorCar, car1.id).load == loads_before[0]
    assert db_session.get(ElevatorCar, car2.id).load == loads_before[1]
    # 回放不新增这批记录
    assert db_session.query(DispatchLog).count() == logs_before


def test_batch_commits_all_when_everything_fits(client, db_session):
    b = Building(name="宽松楼", floors=10)
    db_session.add(b)
    db_session.flush()
    car = ElevatorCar(building_id=b.id, label="E1", floor=1, direction="idle", load=0, capacity=10)
    db_session.add(car)
    c1 = CallTicket(building_id=b.id, floor=1, direction="up", passengers=3, status="waiting")
    c2 = CallTicket(building_id=b.id, floor=1, direction="up", passengers=2, status="waiting")
    db_session.add_all([c1, c2])
    db_session.commit()

    resp = client.post("/api/dispatch/batch", json={"call_ids": [c1.id, c2.id]})

    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert {x["id"] for x in body} == {c1.id, c2.id}
    assert all(x["status"] == "assigned" for x in body)

    db_session.expire_all()
    assert db_session.get(CallTicket, c1.id).status == "assigned"
    assert db_session.get(CallTicket, c2.id).status == "assigned"
    # 同轿厢累计载荷 3+2=5
    assert db_session.get(ElevatorCar, car.id).load == 5
    assert db_session.query(DispatchLog).count() == 2


def test_batch_rejects_duplicate_ids(client, db_session):
    _b, _c1, _c2, c1, _c2t = _make_world(db_session)
    resp = client.post("/api/dispatch/batch", json={"call_ids": [c1.id, c1.id]})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert db_session.query(DispatchLog).count() == 0


def test_single_dispatch_semantics_unchanged_on_reject(client, db_session):
    # 单笔派工语义不变：全部满员时呼梯置 rejected 并写一条 car_id=None 的回放
    b, car1, car2, c1, c2 = _make_world(db_session)
    car1.load = car1.capacity  # 把 1 号也塞满
    db_session.commit()

    resp = client.post("/api/dispatch", json={"call_id": c1.id})

    assert resp.status_code == status.HTTP_409_CONFLICT
    db_session.expire_all()
    assert db_session.get(CallTicket, c1.id).status == "rejected"
    log = db_session.query(DispatchLog).one()
    assert log.call_id == c1.id and log.car_id is None
    # 第二笔不受影响
    assert db_session.get(CallTicket, c2.id).status == "waiting"


def test_single_dispatch_semantics_unchanged_on_success(client, db_session):
    b, car1, car2, c1, c2 = _make_world(db_session)
    resp = client.post("/api/dispatch", json={"call_id": c1.id})
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["status"] == "assigned" and body["assigned_car_id"] == car1.id

    db_session.expire_all()
    assert db_session.get(ElevatorCar, car1.id).load == 1
    assert db_session.get(CallTicket, c2.id).status == "waiting"
    assert db_session.query(DispatchLog).count() == 1
