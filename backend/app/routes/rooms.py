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


def _find_code_owner(db, shed_id: int, room_code: str, exclude_id=None):
    """查找同一菇房内已占用该编号的出菇室（用于给出撞车提示）。"""
    q = db.query(Room).filter(Room.shed_id == shed_id, Room.room_code == room_code)
    if exclude_id is not None:
        q = q.filter(Room.id != exclude_id)
    return q.first()


def _code_conflict_response(owner: Room):
    return (
        jsonify(
            {
                "detail": (
                    f"同一菇房内出菇室编号「{owner.room_code}」已被占用，"
                    f"占用该编号的出菇室 ID 为 {owner.id}，请更换编号"
                ),
                "existingId": owner.id,
            }
        ),
        409,
    )


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
        # 落库统一使用 schema 校验时裁掉两端空白后的编号，不再使用原始输入
        room_code = data["room_code"]
        owner = _find_code_owner(db, data["shed_id"], room_code)
        if owner:
            return _code_conflict_response(owner)
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
            # 并发下靠库级唯一约束兜底：两路同时提交只留一间。
            # 必须先回滚，会话才能继续正常使用（后续清单请求不受影响）。
            db.rollback()
            owner = _find_code_owner(db, data["shed_id"], room_code)
            if owner:
                return _code_conflict_response(owner)
            return jsonify({"detail": "保存冲突，请重试"}), 409
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
        # 改号同样要查重（排除自己），且落库用裁剪后的编号
        room_code = data["room_code"]
        owner = _find_code_owner(db, data["shed_id"], room_code, exclude_id=item.id)
        if owner:
            return _code_conflict_response(owner)
        item.shed_id = data["shed_id"]
        item.room_code = room_code
        item.species = data["species"]
        item.capacity_bags = data["capacity_bags"]
        item.status = data["status"]
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            owner = _find_code_owner(db, data["shed_id"], room_code, exclude_id=item.id)
            if owner:
                return _code_conflict_response(owner)
            return jsonify({"detail": "更新冲突，请重试"}), 409
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
