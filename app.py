"""メモリー廃棄管理 Web システム"""

import csv
import io
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from flask import (
    Flask,
    Response,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(24))
DATABASE = os.environ.get("DATABASE_PATH", "memory_disposal.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# 廃棄方法ごとのコスト（円/個）・環境スコア（低いほど良い）・リスクスコア（低いほど良い）
DISPOSAL_METHODS = {
    "physical": {
        "label": "物理破壊",
        "cost_per_unit": 100,
        "env_score": 3,
        "risk_score": 2,
        "benefit_per_unit": 30,
    },
    "recycle": {
        "label": "リサイクル",
        "cost_per_unit": 60,
        "env_score": 1,
        "risk_score": 1,
        "benefit_per_unit": 90,
    },
    "production_cut": {
        "label": "生産削減",
        "cost_per_unit": 20,
        "env_score": 1,
        "risk_score": 2,
        "benefit_per_unit": 140,
    },
    "aid": {
        "label": "援助転用",
        "cost_per_unit": 130,
        "env_score": 1,
        "risk_score": 2,
        "benefit_per_unit": 70,
    },
}

ENV_LABELS = {0: "影響なし", 1: "低", 2: "中", 3: "高"}
RISK_LABELS = {0: "なし", 1: "低", 2: "中", 3: "高"}

MAX_NAME_LENGTH = 200
MAX_QUANTITY = 1_000_000
MAX_FACILITY_AGE = 100
MAX_NOTE_LENGTH = 500


@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
    except sqlite3.Error:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                facility_age INTEGER NOT NULL DEFAULT 0,
                disposal_method TEXT,
                cost INTEGER DEFAULT 0,
                env_score INTEGER DEFAULT 0,
                risk_score INTEGER DEFAULT 0,
                expected_benefit INTEGER DEFAULT 0,
                net_effect INTEGER DEFAULT 0,
                mitigation_note TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER,
                item_name TEXT,
                action TEXT NOT NULL,
                detail TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
        _ensure_columns(conn)


def _ensure_columns(conn):
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(items)").fetchall()}
    migrations = {
        "facility_age": "ALTER TABLE items ADD COLUMN facility_age INTEGER NOT NULL DEFAULT 0",
        "risk_score": "ALTER TABLE items ADD COLUMN risk_score INTEGER DEFAULT 0",
        "expected_benefit": "ALTER TABLE items ADD COLUMN expected_benefit INTEGER DEFAULT 0",
        "net_effect": "ALTER TABLE items ADD COLUMN net_effect INTEGER DEFAULT 0",
        "mitigation_note": "ALTER TABLE items ADD COLUMN mitigation_note TEXT DEFAULT ''",
    }
    for column, sql in migrations.items():
        if column not in existing:
            conn.execute(sql)
    conn.commit()


def _log_audit(conn, item_id, item_name, action, detail=""):
    """監査ログを記録"""
    conn.execute(
        "INSERT INTO audit_log (item_id, item_name, action, detail) VALUES (?, ?, ?, ?)",
        (item_id, item_name, action, detail),
    )


def recommend_method(item):
    """アイテム情報に基づく推奨廃棄方法"""
    if item["facility_age"] >= 20:
        return "production_cut"
    if item["quantity"] >= 500:
        return "recycle"
    if item["quantity"] >= 100:
        return "aid"
    return "physical"


def calculate_disposal_result(method, quantity):
    info = DISPOSAL_METHODS[method]
    cost = info["cost_per_unit"] * quantity
    benefit = info["benefit_per_unit"] * quantity
    net_effect = benefit - cost
    return cost, benefit, net_effect, info["env_score"], info["risk_score"]


# --- ルート ---


@app.route("/")
def index():
    """廃棄対象在庫のリストアップ"""
    search_query = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()
    method_filter = request.args.get("method", "").strip()

    try:
        with get_db() as conn:
            _ensure_columns(conn)

            query = "SELECT * FROM items WHERE 1=1"
            params = []

            if search_query:
                query += " AND name LIKE ?"
                params.append(f"%{search_query}%")
            if status_filter:
                query += " AND status = ?"
                params.append(status_filter)
            if method_filter:
                query += " AND disposal_method = ?"
                params.append(method_filter)

            query += " ORDER BY created_at DESC"
            items = conn.execute(query, params).fetchall()

            all_items = conn.execute("SELECT * FROM items").fetchall()
    except sqlite3.Error:
        logger.exception("データベースエラーが発生しました")
        flash("データベースエラーが発生しました。", "error")
        items = []
        all_items = []

    recommendations = {item["id"]: recommend_method(item) for item in items}
    return render_template(
        "index.html",
        items=items,
        all_items=all_items,
        methods=DISPOSAL_METHODS,
        env_labels=ENV_LABELS,
        risk_labels=RISK_LABELS,
        recommendations=recommendations,
        search_query=search_query,
        status_filter=status_filter,
        method_filter=method_filter,
    )


@app.route("/add", methods=["POST"])
def add_item():
    """在庫アイテム追加"""
    name = request.form.get("name", "").strip()
    quantity = request.form.get("quantity", "1")
    facility_age = request.form.get("facility_age", "0")

    if not name:
        flash("アイテム名を入力してください。", "error")
        return redirect(url_for("index"))
    if len(name) > MAX_NAME_LENGTH:
        flash(f"アイテム名は{MAX_NAME_LENGTH}文字以下にしてください。", "error")
        return redirect(url_for("index"))

    try:
        quantity = max(1, min(int(quantity), MAX_QUANTITY))
    except ValueError:
        quantity = 1
    try:
        facility_age = max(0, min(int(facility_age), MAX_FACILITY_AGE))
    except ValueError:
        facility_age = 0

    try:
        with get_db() as conn:
            cursor = conn.execute(
                "INSERT INTO items (name, quantity, facility_age) VALUES (?, ?, ?)",
                (name, quantity, facility_age),
            )
            _log_audit(conn, cursor.lastrowid, name, "追加", f"数量: {quantity}, 設備年数: {facility_age}")
            conn.commit()
        logger.info("アイテム追加: %s (数量: %d, 設備年数: %d)", name, quantity, facility_age)
        flash(f"「{name}」を追加しました。", "success")
    except sqlite3.Error:
        logger.exception("アイテム追加に失敗しました")
        flash("アイテムの追加に失敗しました。", "error")
    return redirect(url_for("index"))


@app.route("/delete/<int:item_id>", methods=["POST"])
def delete_item(item_id):
    """アイテム削除"""
    try:
        with get_db() as conn:
            item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if not item:
                flash("アイテムが見つかりません。", "error")
                return redirect(url_for("index"))
            _log_audit(conn, item_id, item["name"], "削除", f"ステータス: {item['status']}")
            conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
            conn.commit()
        logger.info("アイテム削除: ID=%d, 名前=%s", item_id, item["name"])
        flash("アイテムを削除しました。", "success")
    except sqlite3.Error:
        logger.exception("アイテム削除に失敗しました: ID=%d", item_id)
        flash("アイテムの削除に失敗しました。", "error")
    return redirect(url_for("index"))


@app.route("/select_method/<int:item_id>", methods=["POST"])
def select_method(item_id):
    """廃棄方法の選択 + コスト計算 + 環境影響評価"""
    method = request.form.get("method")
    mitigation_note = request.form.get("mitigation_note", "").strip()

    if method not in DISPOSAL_METHODS:
        flash("無効な廃棄方法です。", "error")
        return redirect(url_for("index"))
    if len(mitigation_note) > MAX_NOTE_LENGTH:
        flash(f"リスク対策メモは{MAX_NOTE_LENGTH}文字以下にしてください。", "error")
        return redirect(url_for("index"))

    try:
        with get_db() as conn:
            item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if not item:
                flash("アイテムが見つかりません。", "error")
                return redirect(url_for("index"))
            cost, benefit, net_effect, env_score, risk_score = calculate_disposal_result(
                method, item["quantity"]
            )
            conn.execute(
                """
                UPDATE items
                SET disposal_method = ?, cost = ?, env_score = ?, risk_score = ?, expected_benefit = ?,
                    net_effect = ?, mitigation_note = ?, status = 'pending'
                WHERE id = ?
                """,
                (method, cost, env_score, risk_score, benefit, net_effect, mitigation_note, item_id),
            )
            _log_audit(
                conn,
                item_id,
                item["name"],
                "方法選択",
                f"方法: {DISPOSAL_METHODS[method]['label']}, コスト: ¥{cost:,}",
            )
            conn.commit()
        info = DISPOSAL_METHODS[method]
        flash(
            f"廃棄方法を「{info['label']}」に設定しました。コスト: ¥{cost:,} / 期待効果: ¥{benefit:,} / 純効果: ¥{net_effect:,}",
            "success",
        )
    except sqlite3.Error:
        logger.exception("廃棄方法の設定に失敗しました: ID=%d", item_id)
        flash("廃棄方法の設定に失敗しました。", "error")
    return redirect(url_for("index"))


@app.route("/apply_recommendation/<int:item_id>", methods=["POST"])
def apply_recommendation(item_id):
    """推奨方法を自動適用"""
    try:
        with get_db() as conn:
            item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if not item:
                flash("アイテムが見つかりません。", "error")
                return redirect(url_for("index"))
            method = recommend_method(item)
            cost, benefit, net_effect, env_score, risk_score = calculate_disposal_result(
                method, item["quantity"]
            )
            conn.execute(
                """
                UPDATE items
                SET disposal_method = ?, cost = ?, env_score = ?, risk_score = ?, expected_benefit = ?,
                    net_effect = ?, status = 'pending'
                WHERE id = ?
                """,
                (method, cost, env_score, risk_score, benefit, net_effect, item_id),
            )
            _log_audit(
                conn,
                item_id,
                item["name"],
                "推奨適用",
                f"方法: {DISPOSAL_METHODS[method]['label']}",
            )
            conn.commit()
        flash(f"「{item['name']}」へ推奨方法を適用しました。", "success")
    except sqlite3.Error:
        logger.exception("推奨方法の適用に失敗しました: ID=%d", item_id)
        flash("推奨方法の適用に失敗しました。", "error")
    return redirect(url_for("index"))


@app.route("/auto_plan", methods=["POST"])
def auto_plan():
    """未設定アイテムに推奨方法を一括適用"""
    updated = 0
    try:
        with get_db() as conn:
            items = conn.execute("SELECT * FROM items WHERE disposal_method IS NULL").fetchall()
            for item in items:
                method = recommend_method(item)
                cost, benefit, net_effect, env_score, risk_score = calculate_disposal_result(
                    method, item["quantity"]
                )
                conn.execute(
                    """
                    UPDATE items
                    SET disposal_method = ?, cost = ?, env_score = ?, risk_score = ?, expected_benefit = ?,
                        net_effect = ?, status = 'pending'
                    WHERE id = ?
                    """,
                    (method, cost, env_score, risk_score, benefit, net_effect, item["id"]),
                )
                updated += 1
            if updated:
                _log_audit(conn, None, None, "一括適用", f"{updated}件に推奨方法を適用")
            conn.commit()
        if updated:
            flash(f"{updated}件に推奨方法を一括適用しました。", "success")
        else:
            flash("一括適用の対象はありませんでした。", "info")
    except sqlite3.Error:
        logger.exception("一括適用に失敗しました")
        flash("一括適用に失敗しました。", "error")
    return redirect(url_for("index"))


@app.route("/approve/<int:item_id>", methods=["POST"])
def approve(item_id):
    """廃棄実行の承認"""
    try:
        with get_db() as conn:
            item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if not item:
                flash("アイテムが見つかりません。", "error")
                return redirect(url_for("index"))
            if not item["disposal_method"]:
                flash("先に廃棄方法を選択してください。", "error")
                return redirect(url_for("index"))
            if item["risk_score"] >= 3 and not (item["mitigation_note"] or "").strip():
                flash("高リスク案件の承認にはリスク対策メモが必要です。", "error")
                return redirect(url_for("index"))
            conn.execute("UPDATE items SET status = 'approved' WHERE id = ?", (item_id,))
            _log_audit(conn, item_id, item["name"], "承認", f"方法: {item['disposal_method']}")
            conn.commit()
        logger.info("アイテム承認: ID=%d, 名前=%s", item_id, item["name"])
        flash(f"「{item['name']}」の廃棄を承認しました。", "success")
    except sqlite3.Error:
        logger.exception("承認に失敗しました: ID=%d", item_id)
        flash("承認に失敗しました。", "error")
    return redirect(url_for("index"))


@app.route("/reject/<int:item_id>", methods=["POST"])
def reject(item_id):
    """廃棄の却下"""
    try:
        with get_db() as conn:
            item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if not item:
                flash("アイテムが見つかりません。", "error")
                return redirect(url_for("index"))
            conn.execute("UPDATE items SET status = 'rejected' WHERE id = ?", (item_id,))
            _log_audit(conn, item_id, item["name"], "却下", "")
            conn.commit()
        logger.info("アイテム却下: ID=%d, 名前=%s", item_id, item["name"])
        flash(f"「{item['name']}」の廃棄を却下しました。", "info")
    except sqlite3.Error:
        logger.exception("却下に失敗しました: ID=%d", item_id)
        flash("却下に失敗しました。", "error")
    return redirect(url_for("index"))


@app.route("/reset/<int:item_id>", methods=["POST"])
def reset_item(item_id):
    """却下されたアイテムを未承認に戻す"""
    try:
        with get_db() as conn:
            item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if not item:
                flash("アイテムが見つかりません。", "error")
                return redirect(url_for("index"))
            if item["status"] != "rejected":
                flash("却下されたアイテムのみリセットできます。", "error")
                return redirect(url_for("index"))
            conn.execute(
                "UPDATE items SET status = 'pending', disposal_method = NULL, cost = 0, "
                "env_score = 0, risk_score = 0, expected_benefit = 0, net_effect = 0, "
                "mitigation_note = '' WHERE id = ?",
                (item_id,),
            )
            _log_audit(conn, item_id, item["name"], "リセット", "却下→未承認")
            conn.commit()
        logger.info("アイテムリセット: ID=%d, 名前=%s", item_id, item["name"])
        flash(f"「{item['name']}」をリセットしました。再度廃棄方法を選択してください。", "success")
    except sqlite3.Error:
        logger.exception("リセットに失敗しました: ID=%d", item_id)
        flash("リセットに失敗しました。", "error")
    return redirect(url_for("index"))


@app.route("/execute/<int:item_id>", methods=["POST"])
def execute_disposal(item_id):
    """廃棄実行"""
    try:
        with get_db() as conn:
            item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if not item:
                flash("アイテムが見つかりません。", "error")
                return redirect(url_for("index"))
            if item["status"] != "approved":
                flash("承認済みのアイテムのみ廃棄実行できます。", "error")
                return redirect(url_for("index"))
            conn.execute("UPDATE items SET status = 'executed' WHERE id = ?", (item_id,))
            _log_audit(
                conn,
                item_id,
                item["name"],
                "廃棄実行",
                f"方法: {item['disposal_method']}, コスト: ¥{item['cost']:,}",
            )
            conn.commit()
        logger.info("廃棄実行: ID=%d, 名前=%s", item_id, item["name"])
        flash(f"「{item['name']}」の廃棄を実行しました。", "success")
    except sqlite3.Error:
        logger.exception("廃棄実行に失敗しました: ID=%d", item_id)
        flash("廃棄実行に失敗しました。", "error")
    return redirect(url_for("index"))


@app.route("/export_csv")
def export_csv():
    """在庫データをCSVエクスポート"""
    try:
        with get_db() as conn:
            items = conn.execute("SELECT * FROM items ORDER BY created_at DESC").fetchall()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "ID",
                "アイテム名",
                "数量",
                "設備年数",
                "廃棄方法",
                "コスト",
                "環境影響",
                "リスク",
                "期待効果",
                "純効果",
                "リスク対策メモ",
                "ステータス",
                "作成日時",
            ]
        )
        for item in items:
            method_label = ""
            if item["disposal_method"] and item["disposal_method"] in DISPOSAL_METHODS:
                method_label = DISPOSAL_METHODS[item["disposal_method"]]["label"]
            writer.writerow(
                [
                    item["id"],
                    item["name"],
                    item["quantity"],
                    item["facility_age"],
                    method_label,
                    item["cost"],
                    ENV_LABELS.get(item["env_score"], ""),
                    RISK_LABELS.get(item["risk_score"], ""),
                    item["expected_benefit"],
                    item["net_effect"],
                    item["mitigation_note"] or "",
                    item["status"],
                    item["created_at"],
                ]
            )

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return Response(
            "\ufeff" + output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=memory_disposal_{timestamp}.csv"},
        )
    except sqlite3.Error:
        logger.exception("CSVエクスポートに失敗しました")
        flash("CSVエクスポートに失敗しました。", "error")
        return redirect(url_for("index"))


@app.route("/audit_log")
def audit_log():
    """監査ログの表示"""
    try:
        with get_db() as conn:
            logs = conn.execute(
                "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
    except sqlite3.Error:
        logger.exception("監査ログの取得に失敗しました")
        flash("監査ログの取得に失敗しました。", "error")
        logs = []
    return render_template("audit_log.html", logs=logs)


if __name__ == "__main__":
    init_db()
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() in ("true", "1", "yes")
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=debug_mode, port=port)
