"""`/schedules` — cron refresh per instrument (§7.1, §18.5.5).

The scheduler is only a trigger source, so this router is deliberately boring: it
validates the expression (→ 422 `invalid_cron`), enforces "one enabled schedule per
instrument" (→ 409 `duplicate_schedule`) and stores the next fire time in UTC.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db.session import get_db
from ..errors import DuplicateSchedule, InstrumentNotFound, NotFound
from ..models import Instrument, Schedule
from ..schemas.schedule import ScheduleCreate, ScheduleListResponse, ScheduleOut, SchedulePatch
from ..security import deps
from ..security.principal import Principal
from ..services import audit_service
from ..services import cron as cron_module
from ..services import scheduler as scheduler_service
from ..services.audit_service import AuditTarget

router = APIRouter(prefix="/schedules", tags=["schedules"])


def to_schedule_out(session: Session, schedule: Schedule) -> ScheduleOut:
    return ScheduleOut(
        id=schedule.id,
        instrumentId=schedule.instrument_id,
        symbol=scheduler_service.symbol_of(session, schedule.instrument_id),
        cron=schedule.cron,
        enabled=bool(schedule.enabled),
        minAgeHours=int(schedule.min_age_hours or 12),
        nextRunAt=schedule.next_run_at,
        lastRunId=schedule.last_run_id,
        failureCount=int(schedule.failure_count or 0),
        createdAt=schedule.created_at,
        updatedAt=schedule.updated_at,
    )


def _audit(db: Session, principal: Principal, action: str, schedule: Schedule, detail: dict | None = None) -> None:
    """★v15（§24.3.10）：`schedule.create|update|delete`——定时刷新会花钱，谁动的要留痕。"""
    audit_service.record(
        db,
        principal,
        action,
        AuditTarget("schedule", schedule.id, scheduler_service.symbol_of(db, schedule.instrument_id)),
        {"cron": schedule.cron, "enabled": bool(schedule.enabled), **(detail or {})},
    )
    db.commit()


def _load(db: Session, schedule_id: str) -> Schedule:
    schedule = db.get(Schedule, schedule_id)
    if schedule is None:
        raise NotFound("该定时刷新不存在")
    return schedule


def _assert_no_duplicate(db: Session, instrument_id: str, *, exclude_id: str | None = None) -> None:
    conditions = [Schedule.instrument_id == instrument_id, Schedule.enabled == 1]
    if exclude_id:
        conditions.append(Schedule.id != exclude_id)
    existing = db.execute(select(Schedule).where(*conditions)).scalars().first()
    if existing is not None:
        raise DuplicateSchedule(detail={"scheduleId": existing.id})


@router.get("", response_model=ScheduleListResponse)
def list_schedules(db: Session = Depends(get_db)) -> ScheduleListResponse:
    rows = scheduler_service.list_all(db)
    return ScheduleListResponse(
        items=[to_schedule_out(db, row) for row in rows],
        total=len(rows),
    )


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
def create_schedule(
    payload: ScheduleCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(deps.get_principal),
) -> ScheduleOut:
    # Validate the expression **before** the duplicate check: `422 invalid_cron`
    # names the actual defect, while `409 duplicate_schedule` on a typo'd cron sends
    # the reader hunting for a schedule that has nothing to do with their mistake.
    expression = cron_module.validate(payload.cron)  # raises InvalidCron → 422

    instrument = db.get(Instrument, payload.instrumentId)
    if instrument is None:
        instrument = db.execute(
            select(Instrument).where(Instrument.symbol == payload.instrumentId.upper())
        ).scalar_one_or_none()
    if instrument is None:
        raise InstrumentNotFound(f"标的 {payload.instrumentId} 不存在")
    if payload.enabled:
        _assert_no_duplicate(db, instrument.id)

    try:
        schedule = scheduler_service.create(
            db,
            instrument,
            expression,
            enabled=payload.enabled,
            min_age_hours=payload.minAgeHours,
            owner_id=principal.user_id,  # ★v15：创建者；ticker 触发的 run 以它作 `created_by`
        )
    except IntegrityError as exc:  # the partial unique index is the real guard
        db.rollback()
        raise DuplicateSchedule() from exc
    _audit(db, principal, "schedule.create", schedule)
    return to_schedule_out(db, schedule)


@router.patch("/{schedule_id}", response_model=ScheduleOut)
def patch_schedule(
    schedule_id: str,
    payload: SchedulePatch,
    db: Session = Depends(get_db),
    principal: Principal = Depends(deps.get_principal),
) -> ScheduleOut:
    schedule = _load(db, schedule_id)
    if payload.enabled:
        _assert_no_duplicate(db, schedule.instrument_id, exclude_id=schedule.id)
    try:
        updated = scheduler_service.update(
            db,
            schedule,
            expression=payload.cron,
            enabled=payload.enabled,
            min_age_hours=payload.minAgeHours,
        )
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateSchedule() from exc
    _audit(db, principal, "schedule.update", updated)
    return to_schedule_out(db, updated)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    schedule_id: str, db: Session = Depends(get_db), principal: Principal = Depends(deps.get_principal)
) -> Response:
    schedule = _load(db, schedule_id)
    # 先写审计（flush，不提交），随后与删除同一事务提交：快照在删除前取。
    audit_service.record(
        db,
        principal,
        "schedule.delete",
        AuditTarget("schedule", schedule.id, scheduler_service.symbol_of(db, schedule.instrument_id)),
        {"cron": schedule.cron},
    )
    db.delete(schedule)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
