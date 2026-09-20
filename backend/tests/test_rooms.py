import os
import sys
import tempfile

# 必须在导入 app 之前指向测试库（config 在 import 时读取环境变量）
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["JWT_SECRET"] = "test-secret"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import threading

import pytest
from flask_jwt_extended import create_access_token
from sqlalchemy import UniqueConstraint
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.database import Base, SessionLocal, engine
from app.models.room import Room
from app.models.shed import Shed


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    app = create_app()
    app.config["TESTING"] = True
    db = SessionLocal()
    db.add_all([
        Shed(name="一号菇房", location="A区", notes=None),
        Shed(name="二号菇房", location="B区", notes=None),
    ])
    db.commit()
    db.close()
    with app.app_context():
        token = create_access_token(identity="1")
    c = app.test_client()
    c.test_token = token
    yield c
    Base.metadata.drop_all(bind=engine)


def auth(client):
    return {"Authorization": f"Bearer {client.test_token}"}


def payload(shed_id=1, code="R-01", **kw):
    p = {
        "shedId": shed_id,
        "roomCode": code,
        "species": "香菇",
        "capacityBags": 100,
        "status": "idle",
    }
    p.update(kw)
    return p


def create(client, shed_id=1, code="R-01", **kw):
    return client.post("/api/rooms", json=payload(shed_id, code, **kw), headers=auth(client))


def room_codes_in_db():
    db = SessionLocal()
    try:
        return [(r.shed_id, r.room_code) for r in db.query(Room).order_by(Room.id).all()]
    finally:
        db.close()


# ---------- 空白裁剪 ----------

def test_create_trims_surrounding_whitespace(client):
    res = create(client, code="  R-01  ")
    assert res.status_code == 201
    assert res.get_json()["roomCode"] == "R-01"
    assert room_codes_in_db() == [(1, "R-01")]


def test_whitespace_only_code_rejected(client):
    res = create(client, code="   ")
    assert res.status_code == 400
    assert room_codes_in_db() == []


def test_padded_variant_counts_as_same_room(client):
    assert create(client, code="R-01").status_code == 201
    first_id = client.get("/api/rooms", headers=auth(client)).get_json()[0]["id"]
    res = create(client, code="R-01   ")  # 尾部空格不能混进去
    assert res.status_code == 409
    assert res.get_json()["existingId"] == first_id
    assert room_codes_in_db() == [(1, "R-01")]


# ---------- 新增查重 ----------

def test_duplicate_create_rejected_with_chinese_detail_and_existing_id(client):
    first = create(client, code="R-01").get_json()
    res = create(client, code="R-01")
    assert res.status_code == 409
    body = res.get_json()
    assert "R-01" in body["detail"]
    assert str(first["id"]) in body["detail"]  # 中文说明里指出占用者 id
    assert body["existingId"] == first["id"]
    assert room_codes_in_db() == [(1, "R-01")]


def test_same_code_allowed_in_different_shed(client):
    assert create(client, shed_id=1, code="R-01").status_code == 201
    assert create(client, shed_id=2, code="R-01").status_code == 201
    assert sorted(room_codes_in_db()) == [(1, "R-01"), (2, "R-01")]


# ---------- 改号查重 ----------

def test_update_rename_to_existing_code_rejected(client):
    a = create(client, code="R-01").get_json()
    b = create(client, code="R-02").get_json()
    res = client.put(f"/api/rooms/{b['id']}", json=payload(code="R-01"), headers=auth(client))
    assert res.status_code == 409
    body = res.get_json()
    assert body["existingId"] == a["id"]
    assert str(a["id"]) in body["detail"]
    # 数据库里乙室编号没变
    assert room_codes_in_db() == [(1, "R-01"), (1, "R-02")]


def test_update_rename_to_padded_existing_code_rejected(client):
    a = create(client, code="R-01").get_json()
    b = create(client, code="R-02").get_json()
    res = client.put(f"/api/rooms/{b['id']}", json=payload(code=" R-01 "), headers=auth(client))
    assert res.status_code == 409
    assert res.get_json()["existingId"] == a["id"]


def test_update_own_code_and_trim_ok(client):
    a = create(client, code="R-01").get_json()
    # 改成自己正在用的代号不算撞车
    res = client.put(f"/api/rooms/{a['id']}", json=payload(code="R-01"), headers=auth(client))
    assert res.status_code == 200
    # 改成带空白的新代号：落库已裁剪
    res = client.put(f"/api/rooms/{a['id']}", json=payload(code="  R-09 "), headers=auth(client))
    assert res.status_code == 200
    assert res.get_json()["roomCode"] == "R-09"
    assert room_codes_in_db() == [(1, "R-09")]


# ---------- 被拒之后清单仍正常 ----------

def test_list_works_after_rejected_create(client):
    create(client, code="R-01")
    assert create(client, code="R-01").status_code == 409
    res = client.get("/api/rooms", headers=auth(client))
    assert res.status_code == 200
    assert len(res.get_json()) == 1


def test_list_works_after_rejected_update(client):
    create(client, code="R-01")
    b = create(client, code="R-02").get_json()
    assert client.put(
        f"/api/rooms/{b['id']}", json=payload(code="R-01"), headers=auth(client)
    ).status_code == 409
    res = client.get("/api/rooms", headers=auth(client))
    assert res.status_code == 200
    assert len(res.get_json()) == 2


# ---------- 并发双发只留一间 ----------

def test_concurrent_same_code_only_one_survives(client):
    barrier = threading.Barrier(2)
    results = []

    def hit(code):
        c = client.application.test_client()
        barrier.wait(timeout=10)
        r = c.post("/api/rooms", json=payload(code=code), headers=auth(client))
        results.append((r.status_code, r.get_json()))

    t1 = threading.Thread(target=hit, args=("C-01",))
    t2 = threading.Thread(target=hit, args=(" C-01 ",))  # 带空白变体同时提交
    t1.start(); t2.start()
    t1.join(timeout=30); t2.join(timeout=30)

    statuses = sorted(s for s, _ in results)
    assert statuses == [201, 409]
    conflict = next(body for s, body in results if s == 409)
    created = next(body for s, body in results if s == 201)
    assert conflict["existingId"] == created["id"]
    assert room_codes_in_db() == [(1, "C-01")]


# ---------- 库级约束必须还在 ----------

def test_db_level_unique_constraint_exists_and_enforced(client):
    uqs = [
        arg for arg in Room.__table_args__
        if isinstance(arg, UniqueConstraint)
        and {c.name for c in arg.columns} == {"shed_id", "room_code"}
    ]
    assert uqs, "rooms 表必须保留 (shed_id, room_code) 唯一约束"
    # 直接绕过应用层写库，约束照样生效
    db = SessionLocal()
    try:
        db.add(Room(shed_id=1, room_code="D-01", species="香菇", capacity_bags=1, status="idle"))
        db.commit()
        db.add(Room(shed_id=1, room_code="D-01", species="平菇", capacity_bags=1, status="idle"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()
