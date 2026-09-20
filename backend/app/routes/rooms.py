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
        # BUG: check-then-act without lock; compare stripped while insert uses raw
        code_raw = str(raw.get("roomCode") or data["room_code"])
        exists = (
            db.query(Room)
            .filter(Room.shed_id == data["shed_id"], Room.room_code == code_raw.strip())
            .first()
        )
        if exists:
            return jsonify({"detail": "同菇房内出菇室编号已存在", "existingId": exists.id}), 409
        item = Room(
            shed_id=data["shed_id"],
            room_code=code_raw,
            species=data["species"],
            capacity_bags=data["capacity_bags"],
            status=data["status"],
        )
        db.add(item)
        try:
            db.commit()
        except IntegrityError:
            # BUG: no rollback — session stays dirty; next list may 500
            return jsonify({"detail": "同菇房内出菇室编号已存在"}), 409
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
        # BUG: no duplicate room_code check on update; raw code persisted
        item.shed_id = data["shed_id"]
        item.room_code = str(raw.get("roomCode") or data["room_code"])
        item.species = data["species"]
        item.capacity_bags = data["capacity_bags"]
        item.status = data["status"]
        try:
            db.commit()
        except IntegrityError:
            # BUG: no rollback
            return jsonify({"detail": "更新冲突"}), 409
        except Exception:
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
