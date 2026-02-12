"""メモリー廃棄管理 Web システム"""

import sqlite3
from contextlib import contextmanager
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = "memory-disposal-dev-key"
DATABASE = "memory_disposal.db"

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


@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
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
    with get_db() as conn:
        _ensure_columns(conn)
        items = conn.execute("SELECT * FROM items ORDER BY created_at DESC").fetchall()
    recommendations = {item["id"]: recommend_method(item) for item in items}
    return render_template(
        "index.html",
        items=items,
        methods=DISPOSAL_METHODS,
        env_labels=ENV_LABELS,
        risk_labels=RISK_LABELS,
        recommendations=recommendations,
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
    try:
        quantity = max(1, int(quantity))
    except ValueError:
        quantity = 1
    try:
        facility_age = max(0, int(facility_age))
    except ValueError:
        facility_age = 0
    with get_db() as conn:
        conn.execute(
            "INSERT INTO items (name, quantity, facility_age) VALUES (?, ?, ?)",
            (name, quantity, facility_age),
        )
        conn.commit()
    flash(f"「{name}」を追加しました。", "success")
    return redirect(url_for("index"))


@app.route("/delete/<int:item_id>", methods=["POST"])
def delete_item(item_id):
    """アイテム削除"""
    with get_db() as conn:
        conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.commit()
    flash("アイテムを削除しました。", "success")
    return redirect(url_for("index"))


@app.route("/select_method/<int:item_id>", methods=["POST"])
def select_method(item_id):
    """廃棄方法の選択 + コスト計算 + 環境影響評価"""
    method = request.form.get("method")
    mitigation_note = request.form.get("mitigation_note", "").strip()
    if method not in DISPOSAL_METHODS:
        flash("無効な廃棄方法です。", "error")
        return redirect(url_for("index"))

    with get_db() as conn:
        item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            flash("アイテムが見つかりません。", "error")
            return redirect(url_for("index"))
        cost, benefit, net_effect, env_score, risk_score = calculate_disposal_result(method, item["quantity"])
        conn.execute(
            """
            UPDATE items
            SET disposal_method = ?, cost = ?, env_score = ?, risk_score = ?, expected_benefit = ?,
                net_effect = ?, mitigation_note = ?, status = 'pending'
            WHERE id = ?
            """,
            (method, cost, env_score, risk_score, benefit, net_effect, mitigation_note, item_id),
        )
        conn.commit()
    info = DISPOSAL_METHODS[method]
    flash(
        f"廃棄方法を「{info['label']}」に設定しました。コスト: ¥{cost:,} / 期待効果: ¥{benefit:,} / 純効果: ¥{net_effect:,}",
        "success",
    )
    return redirect(url_for("index"))


@app.route("/apply_recommendation/<int:item_id>", methods=["POST"])
def apply_recommendation(item_id):
    """推奨方法を自動適用"""
    with get_db() as conn:
        item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            flash("アイテムが見つかりません。", "error")
            return redirect(url_for("index"))
        method = recommend_method(item)
        cost, benefit, net_effect, env_score, risk_score = calculate_disposal_result(method, item["quantity"])
        conn.execute(
            """
            UPDATE items
            SET disposal_method = ?, cost = ?, env_score = ?, risk_score = ?, expected_benefit = ?,
                net_effect = ?, status = 'pending'
            WHERE id = ?
            """,
            (method, cost, env_score, risk_score, benefit, net_effect, item_id),
        )
        conn.commit()
    flash(f"「{item['name']}」へ推奨方法を適用しました。", "success")
    return redirect(url_for("index"))


@app.route("/auto_plan", methods=["POST"])
def auto_plan():
    """未設定アイテムに推奨方法を一括適用"""
    updated = 0
    with get_db() as conn:
        items = conn.execute("SELECT * FROM items WHERE disposal_method IS NULL").fetchall()
        for item in items:
            method = recommend_method(item)
            cost, benefit, net_effect, env_score, risk_score = calculate_disposal_result(method, item["quantity"])
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
        conn.commit()
    if updated:
        flash(f"{updated}件に推奨方法を一括適用しました。", "success")
    else:
        flash("一括適用の対象はありませんでした。", "info")
    return redirect(url_for("index"))


@app.route("/approve/<int:item_id>", methods=["POST"])
def approve(item_id):
    """廃棄実行の承認"""
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
        conn.commit()
    flash(f"「{item['name']}」の廃棄を承認しました。", "success")
    return redirect(url_for("index"))


@app.route("/reject/<int:item_id>", methods=["POST"])
def reject(item_id):
    """廃棄の却下"""
    with get_db() as conn:
        item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            flash("アイテムが見つかりません。", "error")
            return redirect(url_for("index"))
        conn.execute("UPDATE items SET status = 'rejected' WHERE id = ?", (item_id,))
        conn.commit()
    flash(f"「{item['name']}」の廃棄を却下しました。", "info")
    return redirect(url_for("index"))


@app.route("/execute/<int:item_id>", methods=["POST"])
def execute_disposal(item_id):
    """廃棄実行"""
    with get_db() as conn:
        item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            flash("アイテムが見つかりません。", "error")
            return redirect(url_for("index"))
        if item["status"] != "approved":
            flash("承認済みのアイテムのみ廃棄実行できます。", "error")
            return redirect(url_for("index"))
        conn.execute("UPDATE items SET status = 'executed' WHERE id = ?", (item_id,))
        conn.commit()
    flash(f"「{item['name']}」の廃棄を実行しました。", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
