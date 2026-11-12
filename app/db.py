"""SQLite storage for raiz.

Everything lives in a single local database. No network, no cloud.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("RAIZ_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "raiz.db"
PAYSLIP_DIR = DATA_DIR / "payslips"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('checking','savings','meal','investment','cash','credit')),
    institution TEXT,
    opening_balance REAL NOT NULL DEFAULT 0,
    color TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('income','expense','saving','investment')),
    group_name TEXT NOT NULL CHECK (group_name IN ('income','essentials','lifestyle','future','other')),
    icon TEXT,
    color TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS payslips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT NOT NULL,
    employer TEXT,
    employee_name TEXT,
    employee_nif TEXT,
    employee_ss TEXT,
    employee_number TEXT,
    professional_category TEXT,
    work_location TEXT,
    pay_date TEXT,
    base_salary REAL NOT NULL DEFAULT 0,
    meal_days REAL NOT NULL DEFAULT 0,
    meal_daily REAL NOT NULL DEFAULT 0,
    meal_allowance REAL NOT NULL DEFAULT 0,
    holiday_subsidy REAL NOT NULL DEFAULT 0,
    christmas_subsidy REAL NOT NULL DEFAULT 0,
    other_earnings REAL NOT NULL DEFAULT 0,
    gross_taxable REAL NOT NULL DEFAULT 0,
    gross_exempt REAL NOT NULL DEFAULT 0,
    irs_rate REAL NOT NULL DEFAULT 0,
    irs_amount REAL NOT NULL DEFAULT 0,
    ss_rate REAL NOT NULL DEFAULT 0,
    ss_amount REAL NOT NULL DEFAULT 0,
    other_deductions REAL NOT NULL DEFAULT 0,
    total_deductions REAL NOT NULL DEFAULT 0,
    net_cash REAL NOT NULL DEFAULT 0,
    net_meal REAL NOT NULL DEFAULT 0,
    total_net REAL NOT NULL DEFAULT 0,
    iban TEXT,
    absence_hours REAL,
    accumulated_pay REAL,
    accumulated_irs REAL,
    file_name TEXT,
    file_path TEXT,
    raw_text TEXT,
    confidence REAL NOT NULL DEFAULT 0,
    warnings_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (month, employer)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    amount REAL NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('income','expense')),
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    category_id INTEGER REFERENCES categories(id),
    description TEXT NOT NULL,
    notes TEXT,
    payslip_id INTEGER REFERENCES payslips(id) ON DELETE SET NULL,
    subscription_id INTEGER REFERENCES subscriptions(id) ON DELETE SET NULL,
    goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_transactions_account ON transactions(account_id);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    cadence TEXT NOT NULL DEFAULT 'monthly' CHECK (cadence IN ('weekly','monthly','quarterly','yearly')),
    next_charge TEXT,
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    url TEXT,
    notes TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    target REAL NOT NULL,
    current REAL NOT NULL DEFAULT 0,
    monthly_contribution REAL NOT NULL DEFAULT 0,
    deadline TEXT,
    kind TEXT NOT NULL DEFAULT 'saving' CHECK (kind IN ('emergency','saving','investment','purchase')),
    color TEXT,
    notes TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT NOT NULL,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    planned REAL NOT NULL DEFAULT 0,
    UNIQUE (month, category_id)
);

CREATE TABLE IF NOT EXISTS payslip_imports (
    id TEXT PRIMARY KEY,
    file_name TEXT,
    file_path TEXT,
    raw_text TEXT,
    parsed_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

ACCOUNTS = [
    ("conta-ordem", "Conta à ordem", "checking", None, "#1F3D2E"),
    ("cartao-refeicao", "Cartão refeição", "meal", None, "#C08A3E"),
    ("poupanca", "Poupança", "savings", None, "#7E9B7A"),
    ("investimentos", "Investimentos", "investment", None, "#2F5D46"),
    ("dinheiro", "Dinheiro", "cash", None, "#8C7A5E"),
]

CATEGORIES = [
    # slug, name, kind, group, icon, color, order
    ("salario", "Salário", "income", "income", "banknote", "#2F5D46", 1),
    ("subsidio-alimentacao", "Subsídio de alimentação", "income", "income", "utensils", "#C08A3E", 2),
    ("outros-rendimentos", "Outros rendimentos", "income", "income", "plus", "#7E9B7A", 3),
    ("casa", "Casa", "expense", "essentials", "home", "#2F5D46", 10),
    ("contas-casa", "Contas da casa", "expense", "essentials", "bolt", "#4A6B57", 11),
    ("supermercado", "Supermercado", "expense", "essentials", "cart", "#5C7A63", 12),
    ("transporte", "Transporte", "expense", "essentials", "car", "#6B8A72", 13),
    ("saude", "Saúde", "expense", "essentials", "heart", "#8A6E5D", 14),
    ("seguros", "Seguros", "expense", "essentials", "shield", "#7E8A6B", 15),
    ("restaurantes", "Restaurantes", "expense", "lifestyle", "utensils", "#C08A3E", 20),
    ("subscricoes", "Subscrições", "expense", "lifestyle", "repeat", "#B65C4A", 21),
    ("compras", "Compras", "expense", "lifestyle", "shopping-bag", "#A8794F", 22),
    ("lazer", "Lazer", "expense", "lifestyle", "sparkles", "#9B7B4A", 23),
    ("viagens", "Viagens", "expense", "lifestyle", "plane", "#7E9B9B", 24),
    ("presentes", "Presentes", "expense", "lifestyle", "gift", "#B07A7A", 25),
    ("fundo-emergencia", "Fundo de emergência", "saving", "future", "shield", "#2F5D46", 30),
    ("investimentos-cat", "Investimentos", "investment", "future", "trending-up", "#1F3D2E", 31),
    ("objetivos", "Objetivos", "saving", "future", "target", "#7E9B7A", 32),
    ("outros-gastos", "Outros", "expense", "other", "more", "#8C7A5E", 40),
]

DEFAULT_SETTINGS = {
    "profile_name": "Guilherme",
    "allocation": json.dumps({"essentials": 50, "lifestyle": 30, "future": 20}),
    "emergency_months": "6",
    "onboarded": "false",
    "app_name": "raiz",
}

GROUP_LABELS = {
    "income": "Rendimento",
    "essentials": "Essenciais",
    "lifestyle": "Estilo de vida",
    "future": "Futuro",
    "other": "Outros",
}

GROUP_DESCRIPTIONS = {
    "essentials": "O que precisas para viver.",
    "lifestyle": "O que escolhes para viver melhor.",
    "future": "O que constróis para amanhã.",
    "other": "O que não encaixa em mais lado nenhum.",
}


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PAYSLIP_DIR.mkdir(parents=True, exist_ok=True)


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.executescript(SCHEMA)
        for slug, name, type_, institution, color in ACCOUNTS:
            conn.execute(
                "INSERT OR IGNORE INTO accounts (slug, name, type, institution, color) VALUES (?, ?, ?, ?, ?)",
                (slug, name, type_, institution, color),
            )
        for slug, name, kind, group, icon, color, order in CATEGORIES:
            conn.execute(
                """INSERT OR IGNORE INTO categories
                   (slug, name, kind, group_name, icon, color, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (slug, name, kind, group, icon, color, order),
            )
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))


def get_settings(conn: sqlite3.Connection) -> dict[str, str]:
    return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")}


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_account(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM accounts WHERE slug = ?", (slug,)).fetchone()


def get_category(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM categories WHERE slug = ?", (slug,)).fetchone()


def all_accounts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM accounts WHERE archived = 0 ORDER BY id").fetchall()


def all_categories(conn: sqlite3.Connection, include_income: bool = False) -> list[sqlite3.Row]:
    if include_income:
        return conn.execute(
            "SELECT * FROM categories WHERE archived = 0 ORDER BY sort_order, id"
        ).fetchall()
    return conn.execute(
        "SELECT * FROM categories WHERE archived = 0 AND kind != 'income' ORDER BY sort_order, id"
    ).fetchall()


def latest_month(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        """SELECT MAX(month) AS m FROM (
             SELECT month FROM payslips
             UNION ALL
             SELECT substr(date, 1, 7) FROM transactions
           )"""
    ).fetchone()
    return row["m"] if row and row["m"] else None


def setting_json(conn: sqlite3.Connection, key: str, default: Any) -> Any:
    raw = get_setting(conn, key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default
