from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Building, CallTicket, DispatchLog, ElevatorCar
from app.schemas.schemas import (
    BatchDispatchRequest,
    BuildingOut,
    CallCreate,
    CallOut,
    CarOut,
    CongestionFloor,
    DispatchRequest,
    LogOut,
)
from app.services.dispatch_engine import (
    CallRequest,
    CarState,
    congestion_by_floor,
    pick_car,
    plan_batch,
)

api_router = APIRouter()


@api_router.get("/health")
def health():
    return {"status": "ok"}


@api_router.get("/buildings", response_model=list[BuildingOut])
def buildings(db: Session = Depends(get_db)):
    return db.scalars(select(Building).order_by(Building.id)).all()


@api_router.get("/cars", response_model=list[CarOut])
def cars(db: Session = Depends(get_db)):
    return db.scalars(select(ElevatorCar).order_by(ElevatorCar.id)).all()


@api_router.get("/calls", response_model=list[CallOut])
def calls(db: Session = Depends(get_db)):
    return db.scalars(select(CallTicket).order_by(CallTicket.id.desc())).all()


@api_router.post("/calls", response_model=CallOut)
def create_call(body: CallCreate, db: Session = Depends(get_db)):
    b = db.get(Building, body.building_id)
    if not b:
        raise HTTPException(404, "楼栋不存在")
    if body.floor > b.floors:
        raise HTTPException(400, "楼层超出")
    if body.direction not in ("up", "down"):
        raise HTTPException(400, "方向无效")
    ticket = CallTicket(
        building_id=body.building_id,
        floor=body.floor,
        direction=body.direction,
        passengers=body.passengers,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


@api_router.post("/dispatch", response_model=CallOut)
def dispatch(body: DispatchRequest, db: Session = Depends(get_db)):
    ticket = db.get(CallTicket, body.call_id)
    if not ticket:
        raise HTTPException(404, "呼梯不存在")
    if ticket.status != "waiting":
        raise HTTPException(400, "呼梯已处理")
    car_rows = db.scalars(
        select(ElevatorCar).where(ElevatorCar.building_id == ticket.building_id)
    ).all()
    cars = [
        CarState(c.id, c.floor, c.direction, c.load, c.capacity) for c in car_rows
    ]
    call = CallRequest(ticket.id, ticket.floor, ticket.direction, ticket.passengers)
    best = pick_car(cars, call)
    if best is None:
        db.add(DispatchLog(call_id=ticket.id, car_id=None, detail="全部轿厢满员，拒绝派工"))
        ticket.status = "rejected"
        db.commit()
        db.refresh(ticket)
        raise HTTPException(409, "无可用轿厢（满员）")
    car = db.get(ElevatorCar, best.car_id)
    assert car
    ticket.status = "assigned"
    ticket.assigned_car_id = car.id
    ticket.score = f"{best.score:.1f}"
    car.load += ticket.passengers
    car.floor = ticket.floor
    car.direction = ticket.direction
    db.add(
        DispatchLog(
            call_id=ticket.id,
            car_id=car.id,
            detail=f"派予 {car.label}，评分 {best.score:.1f}（同向/距离综合）",
        )
    )
    db.commit()
    db.refresh(ticket)
    return ticket


@api_router.post("/dispatch/batch", response_model=list[CallOut])
def dispatch_batch(body: BatchDispatchRequest, db: Session = Depends(get_db)):
    call_ids = body.call_ids
    if len(set(call_ids)) != len(call_ids):
        raise HTTPException(400, "联派呼梯编号重复")

    tickets: list[CallTicket] = []
    for cid in call_ids:  # 保持前端提交顺序，即评分试派顺序
        ticket = db.get(CallTicket, cid)
        if not ticket:
            raise HTTPException(404, f"呼梯 #{cid} 不存在")
        if ticket.status != "waiting":
            raise HTTPException(400, f"呼梯 #{cid} 已处理")
        tickets.append(ticket)

    building_ids = {t.building_id for t in tickets}
    if len(building_ids) > 1:
        raise HTTPException(400, "联派呼梯必须属于同一楼栋")

    car_rows = db.scalars(
        select(ElevatorCar).where(ElevatorCar.building_id == building_ids.pop())
    ).all()
    cars = [
        CarState(c.id, c.floor, c.direction, c.load, c.capacity) for c in car_rows
    ]
    calls = [
        CallRequest(t.id, t.floor, t.direction, t.passengers) for t in tickets
    ]

    # 试派阶段：纯计算，不修改任何 ORM 对象、不写日志
    result = plan_batch(cars, calls)
    if result.failed_call_id is not None:
        # 整批不派：回滚事务（试派阶段为纯计算，此处本无挂起改动），
        # 呼梯状态、轿厢载荷、回放均保持提交前状态
        db.rollback()
        raise HTTPException(
            409,
            f"联派失败：呼梯 #{result.failed_call_id} 无可用轿厢（满员），本批全部不派",
        )

    # 全部可接：一次性落库，单次提交保证原子性
    car_by_id = {c.id: c for c in car_rows}
    ticket_by_id = {t.id: t for t in tickets}
    for a in result.assignments:
        ticket = ticket_by_id[a.call_id]
        car = car_by_id[a.car_id]
        ticket.status = "assigned"
        ticket.assigned_car_id = car.id
        ticket.score = f"{a.score:.1f}"
        car.load += ticket.passengers
        car.floor = ticket.floor
        car.direction = ticket.direction
        db.add(
            DispatchLog(
                call_id=ticket.id,
                car_id=car.id,
                detail=f"联派予 {car.label}，评分 {a.score:.1f}（同向/距离综合）",
            )
        )
    db.commit()
    for t in tickets:
        db.refresh(t)
    return tickets


@api_router.get("/replay", response_model=list[LogOut])
def replay(db: Session = Depends(get_db)):
    return db.scalars(select(DispatchLog).order_by(DispatchLog.id.desc())).all()


@api_router.get("/congestion", response_model=list[CongestionFloor])
def congestion(db: Session = Depends(get_db)):
    waiting = db.scalars(select(CallTicket).where(CallTicket.status == "waiting")).all()
    counts = congestion_by_floor(
        [CallRequest(c.id, c.floor, c.direction, c.passengers) for c in waiting]
    )
    return [
        CongestionFloor(floor=f, passengers=p)
        for f, p in sorted(counts.items(), key=lambda x: -x[1])
    ]
