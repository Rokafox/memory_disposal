"""メモリー廃棄管理 Web システム"""

import sqlite3
from contextlib import contextmanager
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = "memory-disposal-dev-key"
DATABASE = "memory_disposal.db"

# 廃棄方法ごとのコスト（円/個）と環境スコア（低いほど良い）
DISPOSAL_METHODS = {
    "physical": {"label": "物理破壊", "cost_per_unit": 500, "env_score": 3},
    "recycle": {"label": "リサイクル", "cost_per_unit": 300, "env_score": 1},
    "donate": {"label": "援助", "cost_per_unit": 100, "env_score": 0},
}

ENV_LABELS = {0: "影響なし", 1: "低", 2: "中", 3: "高"}


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
                disposal_method TEXT,
                cost INTEGER DEFAULT 0,
                env_score INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


# --- ルート ---


@app.route("/")
def index():
    """廃棄対象在庫のリストアップ"""
    with get_db() as conn:
        items = conn.execute("SELECT * FROM items ORDER BY created_at DESC").fetchall()
    return render_template("index.html", items=items, methods=DISPOSAL_METHODS, env_labels=ENV_LABELS)


@app.route("/add", methods=["POST"])
def add_item():
    """在庫アイテム追加"""
    name = request.form.get("name", "").strip()
    quantity = request.form.get("quantity", "1")
    if not name:
        flash("アイテム名を入力してください。", "error")
        return redirect(url_for("index"))
    try:
        quantity = max(1, int(quantity))
    except ValueError:
        quantity = 1
    with get_db() as conn:
        conn.execute("INSERT INTO items (name, quantity) VALUES (?, ?)", (name, quantity))
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
    if method not in DISPOSAL_METHODS:
        flash("無効な廃棄方法です。", "error")
        return redirect(url_for("index"))

    info = DISPOSAL_METHODS[method]
    with get_db() as conn:
        item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if not item:
            flash("アイテムが見つかりません。", "error")
            return redirect(url_for("index"))
        cost = info["cost_per_unit"] * item["quantity"]
        conn.execute(
            "UPDATE items SET disposal_method = ?, cost = ?, env_score = ?, status = 'pending' WHERE id = ?",
            (method, cost, info["env_score"], item_id),
        )
        conn.commit()
    flash(f"廃棄方法を「{info['label']}」に設定しました。コスト: ¥{cost:,}", "success")
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
