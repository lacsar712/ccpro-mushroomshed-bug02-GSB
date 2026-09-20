"""启动时的幂等结构修复。

rooms 表必须始终带有 (shed_id, room_code) 唯一约束（uq_rooms_shed_code）。
新建库由 Base.metadata.create_all 直接建好；本模块负责给约束缺失期间
创建的旧库补齐：

1. 裁掉已落库编号两端的空白（历史脏数据）；
2. 清理同一菇房内完全重号的行，只保留 id 最小的一间（重号来自旧缺陷，
   不清理则唯一索引建不上）；
3. 唯一索引不存在时补上。

约束是最终防线，任何情况下都不允许靠删约束来"修复"问题。
"""

from sqlalchemy import inspect, text

from app.database import engine

INDEX_NAME = "uq_rooms_shed_code"


def ensure_room_code_unique() -> None:
    if not inspect(engine).has_table("rooms"):
        return  # 表还没建（create_all 之前），无需修复
    with engine.begin() as conn:
        # 1. 规范化历史数据：两端空白落库前就应裁掉，这里修正存量
        conn.execute(text("UPDATE rooms SET room_code = TRIM(room_code)"))

        # 2. 同菇房同编号只留 id 最小的一间，其余视为旧缺陷产生的重复行
        dupes = conn.execute(
            text(
                """
                DELETE r FROM rooms r
                JOIN rooms k
                  ON k.shed_id = r.shed_id
                 AND k.room_code = r.room_code
                 AND k.id < r.id
                """
            )
        ).rowcount
        if dupes:
            print(f"ensure_schema: removed {dupes} duplicate room row(s).")

        # 3. 唯一索引缺失时补上（create_all 不会 ALTER 已存在的表）
        found = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.statistics
                WHERE table_schema = DATABASE()
                  AND table_name = 'rooms'
                  AND index_name = :index_name
                """
            ),
            {"index_name": INDEX_NAME},
        ).scalar_one()
        if not found:
            conn.execute(
                text(
                    f"ALTER TABLE rooms ADD CONSTRAINT {INDEX_NAME} "
                    "UNIQUE (shed_id, room_code)"
                )
            )
            print(
                f"ensure_schema: added unique index {INDEX_NAME} "
                "on rooms(shed_id, room_code)."
            )


if __name__ == "__main__":
    ensure_room_code_unique()
