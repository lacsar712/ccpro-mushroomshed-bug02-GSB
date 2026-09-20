from marshmallow import Schema, fields, validate, pre_load


ROOM_STATUSES = ("fruiting", "idle", "sanitize")


class RoomCreateSchema(Schema):
    shed_id = fields.Int(required=True, data_key="shedId")
    room_code = fields.Str(required=True, data_key="roomCode", validate=validate.Length(min=1, max=32))
    species = fields.Str(required=True, validate=validate.Length(min=1, max=64))
    capacity_bags = fields.Int(required=True, data_key="capacityBags", validate=validate.Range(min=1))
    status = fields.Str(required=True, validate=validate.OneOf(ROOM_STATUSES))

    @pre_load
    def _strip_room_code(self, data, **kwargs):
        # 落库与查重统一使用裁掉两端空白后的编号："R-01 " 与 "R-01" 是同一间。
        # 裁剪发生在校验之前，纯空白的编号会被 Length(min=1) 拦下。
        if isinstance(data, dict) and "roomCode" in data and isinstance(data["roomCode"], str):
            data = {**data, "roomCode": data["roomCode"].strip()}
        return data


class RoomOutSchema(Schema):
    id = fields.Int(dump_only=True)
    shed_id = fields.Int(data_key="shedId")
    room_code = fields.Str(data_key="roomCode")
    species = fields.Str()
    capacity_bags = fields.Int(data_key="capacityBags")
    status = fields.Str()
