"""数据库 Schema 集成测试：验证建表、关键列类型与 DB 规则落地。

零网络：使用内存 SQLite（test_db 夹具）。
"""
from sqlalchemy import inspect

from app.models.database import Base

EXPECTED_TABLES = [
    "relay_stations",
    "models",
    "detection_tasks",
    "detection_results",
    "strategy_results",
    "probe_records",
]


def test_all_tables_created(test_db):
    """create_all 后六张表均存在。"""
    Base.metadata.create_all(bind=test_db)
    tables = inspect(test_db).get_table_names()
    assert all(name in tables for name in EXPECTED_TABLES)


def test_primary_keys_are_integer(test_db):
    """全部表主键为整数类型（禁止字符串主键，规则 3.2）。"""
    Base.metadata.create_all(bind=test_db)
    inspector = inspect(test_db)
    for table in EXPECTED_TABLES:
        primary_columns = inspector.get_pk_constraint(table)["constrained_columns"]
        assert primary_columns == ["id"]
        id_column = next(
            column for column in inspector.get_columns(table) if column["name"] == "id"
        )
        assert "INT" in str(id_column["type"]).upper()


def test_no_foreign_keys(test_db):
    """全部表无外键约束（业务层关联，规则 3.2）。"""
    Base.metadata.create_all(bind=test_db)
    inspector = inspect(test_db)
    for table in EXPECTED_TABLES:
        assert inspector.get_foreign_keys(table) == []


def test_price_columns_are_text(test_db):
    """价格列为 TEXT（精确小数，应用层 Decimal）。"""
    Base.metadata.create_all(bind=test_db)
    columns = {
        column["name"]: column for column in inspect(test_db).get_columns("models")
    }
    assert "TEXT" in str(columns["input_price"]["type"]).upper()
    assert "TEXT" in str(columns["output_price"]["type"]).upper()


def test_timestamp_columns_are_datetime(test_db):
    """时间列为 DATETIME，绝无 DATE 类型（规则 3.2）。"""
    Base.metadata.create_all(bind=test_db)
    inspector = inspect(test_db)
    for table in EXPECTED_TABLES:
        for column in inspector.get_columns(table):
            column_type = str(column["type"]).upper()
            if column["name"].endswith("_at"):
                assert "DATETIME" in column_type
            assert column_type != "DATE"


def test_relay_station_columns_present(test_db):
    """中转站关键列齐全，且密文列命名为 api_key_encrypted（不存明文）。"""
    Base.metadata.create_all(bind=test_db)
    names = {
        column["name"] for column in inspect(test_db).get_columns("relay_stations")
    }
    assert {"name", "protocols", "base_url", "api_key_encrypted", "status"} <= names
    assert "api_key" not in names  # 明文列不应存在
