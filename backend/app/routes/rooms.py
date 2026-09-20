from typing import Optional

from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required
from marshmallow import ValidationError
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models.room import Room
from app.models.shed import Shed
from app.schemas.room import RoomCreateSchema, RoomOutSchema
from app.utils import validation_error_response

bp = Blueprint("rooms", __name__, url_prefix="/api/rooms")

create_schema = RoomCreateSchema()
out_schema = RoomOutSchema()
out_many = RoomOutSchema(many=True)


def _find_by_code(db, shed_id: int, room_code: str, exclude_id: Optional[int] = None):
    q = db.query(Room).filter(Room.shed_id == shed_id, Room.room_code == room_code)
    if exclude_id is not None:
        q = q.filter(Room.id != exclude_id)
    return q.first()


def _code_conflict_response(existing: Optional[Room], room_code: str):
    if existing is not None:
        return jsonify({
            "detail": f"同一菇房内室编号「{room_code}」已被占用，占用它的出菇室 id 为 {existing.id}",
            "existingId": existing.id,
        }), 409
    return jsonify({"detail": f"同一菇房内室编号「{room_code}」已存在"}), 409


@bp.get("")
@jwt_required()
def list_rooms():
    db = SessionLocal()
    try:
        shed_id = request.args.get("shedId", type=int)
        q = db.query(Room)
        if shed_id is not None:
            q = q.filter(Room.shed_id == shed_id)
        rows = q.order_by(Room.id).all()
        return jsonify(out_many.dump(rows))
    finally:
        db.close()


@bp.post("")
@jwt_required()
def create_room():
    db = SessionLocal()
    try:
        raw = request.get_json(silent=True) or {}
        try:
            data = create_schema.load(raw)
        except ValidationError as err:
            return validation_error_response(err)
        shed = db.query(Shed).filter(Shed.id == data["shed_id"]).first()
        if not shed:
            return jsonify({"detail": "菇房不存在"}), 400
        # schema 校验前已裁掉两端空白，查重与落库统一用裁剪后的编号
        room_code = data["room_code"]
        exists = _find_by_code(db, data["shed_id"], room_code)
        if exists:
            return _code_conflict_response(exists, room_code)
        item = Room(
            shed_id=data["shed_id"],
            room_code=room_code,
            species=data["species"],
            capacity_bags=data["capacity_bags"],
            status=data["status"],
        )
        db.add(item)
        try:
            db.commit()
        except IntegrityError:
            # 并发下唯一约束兜底：先回滚让会话恢复可用，再查出已占用的那间
            db.rollback()
            exists = _find_by_code(db, data["shed_id"], room_code)
            return _code_conflict_response(exists, room_code)
        db.refresh(item)
        return jsonify(out_schema.dump(item)), 201
    finally:
        db.close()


@bp.put("/<int:room_id>")
@jwt_required()
def update_room(room_id: int):
    db = SessionLocal()
    try:
        raw = request.get_json(silent=True) or {}
        try:
            data = create_schema.load(raw)
        except ValidationError as err:
            return validation_error_response(err)
        item = db.query(Room).filter(Room.id == room_id).first()
        if not item:
            return jsonify({"detail": "出菇室不存在"}), 404
        shed = db.query(Shed).filter(Shed.id == data["shed_id"]).first()
        if not shed:
            return jsonify({"detail": "菇房不存在"}), 400
        # 改号同样查重（排除自身），落库用裁剪后的编号
        room_code = data["room_code"]
        exists = _find_by_code(db, data["shed_id"], room_code, exclude_id=item.id)
        if exists:
            return _code_conflict_response(exists, room_code)
        item.shed_id = data["shed_id"]
        item.room_code = room_code
        item.species = data["species"]
        item.capacity_bags = data["capacity_bags"]
        item.status = data["status"]
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            exists = _find_by_code(db, data["shed_id"], room_code, exclude_id=item.id)
            return _code_conflict_response(exists, room_code)
        except Exception:
            db.rollback()
            return jsonify({"detail": "更新失败"}), 500
        db.refresh(item)
        return jsonify(out_schema.dump(item))
    finally:
        db.close()


@bp.delete("/<int:room_id>")
@jwt_required()
def delete_room(room_id: int):
    db = SessionLocal()
    try:
        item = db.query(Room).filter(Room.id == room_id).first()
        if not item:
            return jsonify({"detail": "出菇室不存在"}), 404
        db.delete(item)
        db.commit()
        return "", 204
    finally:
        db.close()
