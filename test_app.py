"""メモリー廃棄管理システム テスト"""

import os
import sqlite3
import tempfile

import pytest

os.environ["SECRET_KEY"] = "test-secret-key"

from app import (
    DISPOSAL_METHODS,
    app,
    calculate_disposal_result,
    init_db,
    recommend_method,
)


@pytest.fixture
def client():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    app.config["TESTING"] = True

    import app as app_module

    app_module.DATABASE = db_path

    with app.test_client() as client:
        with app.app_context():
            init_db()
        yield client

    os.close(db_fd)
    os.unlink(db_path)


# --- recommend_method テスト ---


class TestRecommendMethod:
    def test_old_facility_recommends_production_cut(self):
        item = {"facility_age": 25, "quantity": 10}
        assert recommend_method(item) == "production_cut"

    def test_exactly_20_years_recommends_production_cut(self):
        item = {"facility_age": 20, "quantity": 10}
        assert recommend_method(item) == "production_cut"

    def test_large_quantity_recommends_recycle(self):
        item = {"facility_age": 5, "quantity": 500}
        assert recommend_method(item) == "recycle"

    def test_medium_quantity_recommends_aid(self):
        item = {"facility_age": 5, "quantity": 100}
        assert recommend_method(item) == "aid"

    def test_small_quantity_recommends_physical(self):
        item = {"facility_age": 5, "quantity": 50}
        assert recommend_method(item) == "physical"

    def test_facility_age_takes_priority_over_quantity(self):
        item = {"facility_age": 20, "quantity": 1000}
        assert recommend_method(item) == "production_cut"

    def test_quantity_500_boundary(self):
        item = {"facility_age": 5, "quantity": 499}
        assert recommend_method(item) == "aid"

    def test_quantity_100_boundary(self):
        item = {"facility_age": 5, "quantity": 99}
        assert recommend_method(item) == "physical"


# --- calculate_disposal_result テスト ---


class TestCalculateDisposalResult:
    def test_physical_calculation(self):
        cost, benefit, net_effect, env_score, risk_score = calculate_disposal_result("physical", 10)
        assert cost == 1000
        assert benefit == 300
        assert net_effect == -700
        assert env_score == 3
        assert risk_score == 2

    def test_recycle_calculation(self):
        cost, benefit, net_effect, env_score, risk_score = calculate_disposal_result("recycle", 100)
        assert cost == 6000
        assert benefit == 9000
        assert net_effect == 3000
        assert env_score == 1
        assert risk_score == 1

    def test_production_cut_calculation(self):
        cost, benefit, net_effect, env_score, risk_score = calculate_disposal_result(
            "production_cut", 50
        )
        assert cost == 1000
        assert benefit == 7000
        assert net_effect == 6000
        assert env_score == 1
        assert risk_score == 2

    def test_aid_calculation(self):
        cost, benefit, net_effect, env_score, risk_score = calculate_disposal_result("aid", 200)
        assert cost == 26000
        assert benefit == 14000
        assert net_effect == -12000
        assert env_score == 1
        assert risk_score == 2

    def test_single_unit(self):
        cost, benefit, net_effect, env_score, risk_score = calculate_disposal_result("recycle", 1)
        assert cost == 60
        assert benefit == 90
        assert net_effect == 30


# --- ルートテスト ---


class TestIndexRoute:
    def test_index_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "メモリー廃棄管理システム".encode() in resp.data

    def test_index_with_search(self, client):
        client.post("/add", data={"name": "DDR4 8GB", "quantity": "10", "facility_age": "5"})
        client.post("/add", data={"name": "DDR5 16GB", "quantity": "20", "facility_age": "2"})
        resp = client.get("/?q=DDR4")
        assert resp.status_code == 200
        assert "DDR4 8GB".encode() in resp.data

    def test_index_with_status_filter(self, client):
        client.post("/add", data={"name": "Test Item", "quantity": "10", "facility_age": "5"})
        resp = client.get("/?status=pending")
        assert resp.status_code == 200


class TestAddItem:
    def test_add_item_success(self, client):
        resp = client.post(
            "/add",
            data={"name": "DDR4 8GB", "quantity": "100", "facility_age": "5"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "DDR4 8GB".encode() in resp.data

    def test_add_item_empty_name(self, client):
        resp = client.post(
            "/add",
            data={"name": "", "quantity": "1", "facility_age": "0"},
            follow_redirects=True,
        )
        assert "アイテム名を入力してください".encode() in resp.data

    def test_add_item_invalid_quantity_defaults_to_1(self, client):
        resp = client.post(
            "/add",
            data={"name": "Test", "quantity": "abc", "facility_age": "0"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_add_item_name_too_long(self, client):
        resp = client.post(
            "/add",
            data={"name": "A" * 201, "quantity": "1", "facility_age": "0"},
            follow_redirects=True,
        )
        assert "200文字以下".encode() in resp.data

    def test_add_item_quantity_clamped(self, client):
        resp = client.post(
            "/add",
            data={"name": "Test", "quantity": "2000000", "facility_age": "0"},
            follow_redirects=True,
        )
        assert resp.status_code == 200


class TestDeleteItem:
    def test_delete_item_success(self, client):
        client.post("/add", data={"name": "ToDelete", "quantity": "1", "facility_age": "0"})
        resp = client.post("/delete/1", follow_redirects=True)
        assert resp.status_code == 200
        assert "削除しました".encode() in resp.data

    def test_delete_nonexistent_item(self, client):
        resp = client.post("/delete/999", follow_redirects=True)
        assert "見つかりません".encode() in resp.data


class TestSelectMethod:
    def test_select_method_success(self, client):
        client.post("/add", data={"name": "Test", "quantity": "100", "facility_age": "5"})
        resp = client.post(
            "/select_method/1",
            data={"method": "recycle", "mitigation_note": ""},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "リサイクル".encode() in resp.data

    def test_select_invalid_method(self, client):
        client.post("/add", data={"name": "Test", "quantity": "100", "facility_age": "5"})
        resp = client.post(
            "/select_method/1",
            data={"method": "invalid_method"},
            follow_redirects=True,
        )
        assert "無効な廃棄方法".encode() in resp.data

    def test_select_method_nonexistent_item(self, client):
        resp = client.post(
            "/select_method/999",
            data={"method": "recycle"},
            follow_redirects=True,
        )
        assert "見つかりません".encode() in resp.data

    def test_select_method_note_too_long(self, client):
        client.post("/add", data={"name": "Test", "quantity": "10", "facility_age": "5"})
        resp = client.post(
            "/select_method/1",
            data={"method": "recycle", "mitigation_note": "X" * 501},
            follow_redirects=True,
        )
        assert "500文字以下".encode() in resp.data


class TestApplyRecommendation:
    def test_apply_recommendation_success(self, client):
        client.post("/add", data={"name": "Test", "quantity": "600", "facility_age": "5"})
        resp = client.post("/apply_recommendation/1", follow_redirects=True)
        assert resp.status_code == 200
        assert "推奨方法を適用".encode() in resp.data

    def test_apply_recommendation_nonexistent(self, client):
        resp = client.post("/apply_recommendation/999", follow_redirects=True)
        assert "見つかりません".encode() in resp.data


class TestAutoPlan:
    def test_auto_plan_applies_methods(self, client):
        client.post("/add", data={"name": "Item1", "quantity": "50", "facility_age": "5"})
        client.post("/add", data={"name": "Item2", "quantity": "600", "facility_age": "3"})
        resp = client.post("/auto_plan", follow_redirects=True)
        assert resp.status_code == 200
        assert "2件に推奨方法を一括適用".encode() in resp.data

    def test_auto_plan_no_items(self, client):
        resp = client.post("/auto_plan", follow_redirects=True)
        assert "対象はありません".encode() in resp.data


class TestApprovalWorkflow:
    def _create_and_plan_item(self, client, name="Test", quantity="100", age="5"):
        client.post("/add", data={"name": name, "quantity": quantity, "facility_age": age})
        client.post("/apply_recommendation/1")

    def test_approve_success(self, client):
        self._create_and_plan_item(client)
        resp = client.post("/approve/1", follow_redirects=True)
        assert "承認しました".encode() in resp.data

    def test_approve_without_method(self, client):
        client.post("/add", data={"name": "Test", "quantity": "10", "facility_age": "5"})
        resp = client.post("/approve/1", follow_redirects=True)
        assert "廃棄方法を選択してください".encode() in resp.data

    def test_reject_success(self, client):
        self._create_and_plan_item(client)
        resp = client.post("/reject/1", follow_redirects=True)
        assert "却下しました".encode() in resp.data

    def test_execute_after_approve(self, client):
        self._create_and_plan_item(client)
        client.post("/approve/1")
        resp = client.post("/execute/1", follow_redirects=True)
        assert "廃棄を実行しました".encode() in resp.data

    def test_execute_without_approve(self, client):
        self._create_and_plan_item(client)
        resp = client.post("/execute/1", follow_redirects=True)
        assert "承認済みのアイテムのみ".encode() in resp.data

    def test_approve_nonexistent(self, client):
        resp = client.post("/approve/999", follow_redirects=True)
        assert "見つかりません".encode() in resp.data


class TestResetItem:
    def test_reset_rejected_item(self, client):
        client.post("/add", data={"name": "Test", "quantity": "100", "facility_age": "5"})
        client.post("/apply_recommendation/1")
        client.post("/reject/1")
        resp = client.post("/reset/1", follow_redirects=True)
        assert "リセットしました".encode() in resp.data

    def test_reset_non_rejected_item(self, client):
        client.post("/add", data={"name": "Test", "quantity": "100", "facility_age": "5"})
        resp = client.post("/reset/1", follow_redirects=True)
        assert "却下されたアイテムのみ".encode() in resp.data

    def test_reset_nonexistent(self, client):
        resp = client.post("/reset/999", follow_redirects=True)
        assert "見つかりません".encode() in resp.data


class TestExportCSV:
    def test_export_csv_empty(self, client):
        resp = client.get("/export_csv")
        assert resp.status_code == 200
        assert "text/csv" in resp.content_type

    def test_export_csv_with_data(self, client):
        client.post("/add", data={"name": "DDR4", "quantity": "100", "facility_age": "5"})
        client.post("/apply_recommendation/1")
        resp = client.get("/export_csv")
        assert resp.status_code == 200
        assert b"DDR4" in resp.data


class TestAuditLog:
    def test_audit_log_page(self, client):
        resp = client.get("/audit_log")
        assert resp.status_code == 200
        assert "監査ログ".encode() in resp.data

    def test_audit_log_records_actions(self, client):
        client.post("/add", data={"name": "AuditTest", "quantity": "10", "facility_age": "5"})
        resp = client.get("/audit_log")
        assert resp.status_code == 200
        assert "AuditTest".encode() in resp.data
        assert "追加".encode() in resp.data
