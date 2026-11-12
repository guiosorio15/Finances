"""Queries and domain actions used by the views."""
from __future__ import annotations

import calendar
import json
import sqlite3
from datetime import date
from typing import Any

from app.db import get_account, get_category, all_categories
from app.utils import (
    add_cadence,
    annual_equivalent,
    days_until,
    eur,
    month_add,
    month_bounds,
    month_label,
    month_range,
    monthly_equivalent,
    pct,
    safe_div,
    today_iso,
)


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def month_summary(conn: sqlite3.Connection, month: str) -> dict[str, Any]:
    start, end = month_bounds(month)
    row = conn.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN t.type = 'income' THEN t.amount END), 0) AS income,
          COALESCE(SUM(CASE WHEN t.type = 'income' AND a.type = 'meal' THEN t.amount END), 0) AS income_meal,
          COALESCE(SUM(CASE WHEN t.type = 'income' AND a.type != 'meal' THEN t.amount END), 0) AS income_cash,
          COALESCE(SUM(CASE WHEN t.type = 'expense' AND COALESCE(c.kind, 'expense') = 'expense' THEN -t.amount END), 0) AS expenses,
          COALESCE(SUM(CASE WHEN t.type = 'expense' AND COALESCE(c.kind, 'expense') IN ('saving','investment') THEN -t.amount END), 0) AS future,
          COALESCE(SUM(CASE WHEN t.type = 'expense' AND a.type = 'meal' THEN -t.amount END), 0) AS expenses_meal,
          COALESCE(SUM(CASE WHEN t.type = 'expense' AND a.type != 'meal' THEN -t.amount END), 0) AS expenses_cash,
          COUNT(t.id) AS transaction_count
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        LEFT JOIN categories c ON c.id = t.category_id
        WHERE t.date >= ? AND t.date <= ?
        """,
        (start, end),
    ).fetchone()
    data = dict(row)
    data["income"] = float(data["income"] or 0)
    data["income_meal"] = float(data["income_meal"] or 0)
    data["income_cash"] = float(data["income_cash"] or 0)
    data["expenses"] = float(data["expenses"] or 0)
    data["future"] = float(data["future"] or 0)
    data["expenses_meal"] = float(data["expenses_meal"] or 0)
    data["expenses_cash"] = float(data["expenses_cash"] or 0)
    data["available"] = data["income"] - data["expenses"] - data["future"]
    data["savings_rate"] = pct(data["future"], data["income"])
    data["expense_rate"] = pct(data["expenses"], data["income"])
    data["budget_planned"] = float(
        conn.execute(
            "SELECT COALESCE(SUM(planned), 0) FROM budgets WHERE month = ?", (month,)
        ).fetchone()[0]
        or 0
    )
    data["budget_unassigned"] = data["income"] - data["budget_planned"]
    data["budget_spent"] = data["expenses"] + data["future"]
    data["month"] = month
    return data


def month_transactions(
    conn: sqlite3.Connection,
    month: str,
    *,
    type_filter: str = "",
    account_id: int | None = None,
    category_id: int | None = None,
    search: str = "",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    start, end = month_bounds(month)
    sql = """
        SELECT t.*, c.name AS category_name, c.icon AS category_icon,
               c.color AS category_color, c.kind AS category_kind,
               c.group_name AS category_group,
               a.name AS account_name, a.type AS account_type, a.color AS account_color
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        LEFT JOIN categories c ON c.id = t.category_id
        WHERE t.date >= ? AND t.date <= ?
    """
    params: list[Any] = [start, end]
    if type_filter == "income":
        sql += " AND t.type = 'income'"
    elif type_filter == "expense":
        sql += " AND t.type = 'expense' AND COALESCE(c.kind, 'expense') = 'expense'"
    elif type_filter == "future":
        sql += " AND t.type = 'expense' AND c.kind IN ('saving','investment')"
    if account_id:
        sql += " AND t.account_id = ?"
        params.append(account_id)
    if category_id:
        sql += " AND t.category_id = ?"
        params.append(category_id)
    if search:
        sql += " AND (t.description LIKE ? OR t.notes LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    sql += " ORDER BY t.date DESC, t.id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def category_totals(conn: sqlite3.Connection, month: str) -> list[dict[str, Any]]:
    start, end = month_bounds(month)
    rows = conn.execute(
        """
        SELECT COALESCE(c.id, 0) AS id, COALESCE(c.name, 'Sem categoria') AS name,
               COALESCE(c.color, '#8C7A5E') AS color,
               COALESCE(c.icon, 'more') AS icon,
               COALESCE(c.group_name, 'other') AS group_name,
               SUM(-t.amount) AS total
        FROM transactions t
        LEFT JOIN categories c ON c.id = t.category_id
        WHERE t.type = 'expense' AND t.date >= ? AND t.date <= ?
          AND COALESCE(c.kind, 'expense') = 'expense'
        GROUP BY c.id
        ORDER BY total DESC
        """,
        (start, end),
    ).fetchall()
    totals = [dict(row) for row in rows]
    grand = sum(item["total"] for item in totals) or 1
    for item in totals:
        item["share"] = item["total"] / grand * 100
    return totals


def cashflow_series(conn: sqlite3.Connection, end_month: str, count: int = 6) -> dict[str, Any]:
    months = month_range(end_month, count)
    rows = conn.execute(
        """
        SELECT substr(t.date, 1, 7) AS month,
               COALESCE(SUM(CASE WHEN t.type = 'income' THEN t.amount END), 0) AS income,
               COALESCE(SUM(CASE WHEN t.type = 'expense' AND COALESCE(c.kind,'expense') = 'expense' THEN -t.amount END), 0) AS expenses
        FROM transactions t
        LEFT JOIN categories c ON c.id = t.category_id
        WHERE t.date >= ? AND t.date <= ?
        GROUP BY substr(t.date, 1, 7)
        """,
        (month_bounds(months[0])[0], month_bounds(months[-1])[1]),
    ).fetchall()
    by_month = {row["month"]: dict(row) for row in rows}
    series = []
    for month in months:
        item = by_month.get(month, {"income": 0, "expenses": 0})
        series.append(
            {
                "month": month,
                "label": month_label(month, short=True),
                "income": float(item["income"] or 0),
                "expenses": float(item["expenses"] or 0),
            }
        )
    max_value = max([max(i["income"], i["expenses"]) for i in series] + [0])
    for item in series:
        item["income_pct"] = round(safe_div(item["income"], max_value) * 100, 1) if max_value else 0
        item["expense_pct"] = round(safe_div(item["expenses"], max_value) * 100, 1) if max_value else 0
    return {"series": series, "max": max_value, "has_data": max_value > 0}


def budget_data(conn: sqlite3.Connection, month: str) -> dict[str, Any]:
    start, end = month_bounds(month)
    income = month_summary(conn, month)["income"]
    categories = all_categories(conn)
    budgets = {
        row["category_id"]: float(row["planned"] or 0)
        for row in conn.execute("SELECT category_id, planned FROM budgets WHERE month = ?", (month,))
    }
    spent_rows = conn.execute(
        """
        SELECT category_id, COALESCE(SUM(-amount), 0) AS spent
        FROM transactions
        WHERE type = 'expense' AND date >= ? AND date <= ?
        GROUP BY category_id
        """,
        (start, end),
    ).fetchall()
    spent = {row["category_id"]: float(row["spent"] or 0) for row in spent_rows}

    groups: dict[str, dict[str, Any]] = {}
    for cat in categories:
        group_key = cat["group_name"]
        groups.setdefault(
            group_key,
            {
                "key": group_key,
                "label": {"essentials": "Essenciais", "lifestyle": "Estilo de vida", "future": "Futuro", "other": "Outros"}.get(
                    group_key, group_key
                ),
                "description": {
                    "essentials": "O que precisas para viver.",
                    "lifestyle": "O que escolhes para viver melhor.",
                    "future": "O que constróis para amanhã.",
                    "other": "O que não encaixa em mais lado nenhum.",
                }.get(group_key, ""),
                "categories": [],
                "planned": 0.0,
                "spent": 0.0,
            },
        )
        planned = budgets.get(cat["id"], 0.0)
        used = spent.get(cat["id"], 0.0)
        item = {
            "id": cat["id"],
            "slug": cat["slug"],
            "name": cat["name"],
            "icon": cat["icon"],
            "color": cat["color"],
            "kind": cat["kind"],
            "group": cat["group_name"],
            "planned": planned,
            "spent": used,
            "remaining": planned - used,
            "progress": min(100, pct(used, planned)) if planned else (100 if used else 0),
        }
        groups[group_key]["categories"].append(item)
        groups[group_key]["planned"] += planned
        groups[group_key]["spent"] += used

    group_list = [groups[k] for k in ("essentials", "lifestyle", "future", "other") if k in groups]
    total_planned = sum(g["planned"] for g in group_list)
    total_spent = sum(g["spent"] for g in group_list)
    return {
        "month": month,
        "groups": group_list,
        "income": income,
        "planned": total_planned,
        "spent": total_spent,
        "unassigned": income - total_planned,
        "progress": min(100, pct(total_spent, income)) if income else 0,
    }


def allocation(conn: sqlite3.Connection) -> dict[str, int]:
    raw = conn.execute("SELECT value FROM settings WHERE key = 'allocation'").fetchone()
    try:
        data = json.loads(raw["value"]) if raw else {}
    except (TypeError, ValueError):
        data = {}
    return {
        "essentials": int(data.get("essentials", 50)),
        "lifestyle": int(data.get("lifestyle", 30)),
        "future": int(data.get("future", 20)),
    }


def subscriptions_data(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT s.*, c.name AS category_name, c.color AS category_color,
               c.icon AS category_icon, a.name AS account_name
        FROM subscriptions s
        LEFT JOIN categories c ON c.id = s.category_id
        LEFT JOIN accounts a ON a.id = s.account_id
        WHERE s.active = 1
        ORDER BY CASE WHEN s.next_charge IS NULL THEN 1 ELSE 0 END, s.next_charge, s.name
        """
    ).fetchall()
    items = []
    monthly_total = 0.0
    annual_total = 0.0
    for row in rows:
        item = dict(row)
        item["monthly"] = monthly_equivalent(float(row["amount"]), row["cadence"])
        item["annual"] = annual_equivalent(float(row["amount"]), row["cadence"])
        item["days"] = days_until(row["next_charge"])
        item["overdue"] = item["days"] is not None and item["days"] < 0
        monthly_total += item["monthly"]
        annual_total += item["annual"]
        items.append(item)
    upcoming = [item for item in items if item["next_charge"]]
    next_item = upcoming[0] if upcoming else None
    return {
        "items": items,
        "monthly_total": monthly_total,
        "annual_total": annual_total,
        "next": next_item,
        "count": len(items),
    }


def goals_data(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT * FROM goals WHERE archived = 0 ORDER BY id").fetchall()
    items = []
    total_current = 0.0
    total_target = 0.0
    monthly_total = 0.0
    for row in rows:
        item = dict(row)
        target = float(row["target"] or 0)
        current = float(row["current"] or 0)
        monthly = float(row["monthly_contribution"] or 0)
        item["progress"] = min(100, pct(current, target)) if target else 0
        item["remaining"] = max(0, target - current)
        item["months_left"] = (
            int((target - current) / monthly + 0.999) if monthly > 0 and target > current else 0
        )
        if item["months_left"]:
            projected = month_add(date.today().strftime("%Y-%m"), item["months_left"])
            item["projected"] = month_label(projected)
        else:
            item["projected"] = None
        total_current += current
        total_target += target
        monthly_total += monthly
        items.append(item)
    return {
        "items": items,
        "total_current": total_current,
        "total_target": total_target,
        "monthly_total": monthly_total,
        "progress": min(100, pct(total_current, total_target)) if total_target else 0,
    }


def payslips_list(conn: sqlite3.Connection, limit: int = 24) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM payslips ORDER BY month DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]


def account_balances(conn: sqlite3.Connection) -> dict[int, float]:
    rows = conn.execute(
        """
        SELECT a.id, a.opening_balance + COALESCE(SUM(t.amount), 0) AS balance
        FROM accounts a
        LEFT JOIN transactions t ON t.account_id = a.id
        GROUP BY a.id
        """
    ).fetchall()
    return {row["id"]: float(row["balance"] or 0) for row in rows}


def payslip_data(conn: sqlite3.Connection, payslip_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM payslips WHERE id = ?", (payslip_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["gross_total"] = float(item["gross_taxable"] or 0) + float(item["gross_exempt"] or 0)
    item["deduction_rate"] = pct(item["total_deductions"], item["gross_taxable"])
    item["net_rate"] = pct(item["total_net"], item["gross_total"])
    item["meal_share"] = pct(item["net_meal"], item["total_net"])
    item["annual_projection_gross"] = float(item["gross_total"] or 0) * 12
    item["annual_projection_net"] = float(item["total_net"] or 0) * 12
    return item


def dashboard_insights(
    conn: sqlite3.Connection,
    month: str,
    summary: dict[str, Any],
    subscriptions: dict[str, Any],
    goals: dict[str, Any],
    payslip: dict[str, Any] | None,
) -> list[dict[str, str]]:
    insights: list[dict[str, str]] = []
    if payslip and float(payslip.get("net_meal") or 0) > 0:
        insights.append(
            {
                "tag": "Cartão refeição",
                "title": f"{eur(payslip['net_meal'], 0)} em comida, sem descontos.",
                "body": "Usa o cartão refeição primeiro nas compras de supermercado. É dinheiro que já é teu e liberta cash para o resto do mês.",
                "icon": "utensils",
                "tone": "ochre",
            }
        )
    if payslip and float(payslip.get("total_deductions") or 0) > 0:
        insights.append(
            {
                "tag": "Do bruto ao líquido",
                "title": f"Ficaste com {payslip['net_rate']:.0f}% do que foi acordado.",
                "body": f"IRS e Segurança Social levaram {eur(payslip['total_deductions'])}. O líquido em conta foi {eur(payslip['net_cash'])}; o cartão refeição acrescenta {eur(payslip['net_meal'])}.",
                "icon": "receipt",
                "tone": "green",
            }
        )
    if summary["future"] == 0 and summary["income"] > 0:
        suggested = round(summary["income"] * 0.2, 2)
        insights.append(
            {
                "tag": "Primeiro passo",
                "title": "Ainda não há nada alocado ao futuro.",
                "body": f"Começa com 20% ({eur(suggested)}) ou com um valor que não te aperte. O hábito vale mais do que o montante inicial.",
                "icon": "sprout",
                "tone": "sage",
            }
        )
    elif summary["future"] > 0:
        insights.append(
            {
                "tag": "A caminho",
                "title": f"Já puseste {eur(summary['future'])} a trabalhar para ti.",
                "body": f"É {summary['savings_rate']:.0f}% do rendimento deste mês. A meta sugerida é 20%.",
                "icon": "trending-up",
                "tone": "green",
            }
        )
    if subscriptions["monthly_total"] > 0:
        share = pct(subscriptions["monthly_total"], summary["income"]) if summary["income"] else 0
        insights.append(
            {
                "tag": "Subscrições",
                "title": f"{eur(subscriptions['monthly_total'])} por mês em cobranças recorrentes.",
                "body": f"Representam {share:.0f}% do rendimento. Revisita a lista uma vez por trimestre e corta o que já não usas.",
                "icon": "repeat",
                "tone": "terracotta",
            }
        )
    if summary["budget_unassigned"] > 0.01 and summary["income"] > 0:
        insights.append(
            {
                "tag": "Orçamento",
                "title": f"{eur(summary['budget_unassigned'])} ainda sem destino.",
                "body": "Atribui este valor a envelopes antes de o gastar. Um euro sem nome é um euro que desaparece.",
                "icon": "layers",
                "tone": "ochre",
            }
        )
    if summary["income"] == 0:
        insights.append(
            {
                "tag": "Começar",
                "title": "Este mês ainda está em branco.",
                "body": "Importa o recibo de vencimento para o raiz preencher o rendimento e os descontos automaticamente.",
                "icon": "upload",
                "tone": "green",
            }
        )
    return insights[:3]


def create_payslip_and_transactions(
    conn: sqlite3.Connection, fields: dict[str, Any], file_name: str | None, file_path: str | None,
    raw_text: str, confidence: float, warnings: list[str]
) -> int:
    month = fields.get("month") or date.today().strftime("%Y-%m")
    employer = fields.get("employer") or "Sem empregador"
    cur = conn.execute(
        """
        INSERT INTO payslips (
          month, employer, employee_name, employee_nif, employee_ss, employee_number,
          professional_category, work_location, pay_date, base_salary, meal_days, meal_daily,
          meal_allowance, holiday_subsidy, christmas_subsidy, other_earnings, gross_taxable,
          gross_exempt, irs_rate, irs_amount, ss_rate, ss_amount, other_deductions,
          total_deductions, net_cash, net_meal, total_net, iban, absence_hours,
          accumulated_pay, accumulated_irs, file_name, file_path, raw_text, confidence, warnings_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            month,
            employer,
            fields.get("employee_name"),
            fields.get("employee_nif"),
            fields.get("employee_ss"),
            fields.get("employee_number"),
            fields.get("professional_category"),
            fields.get("work_location"),
            fields.get("pay_date"),
            float(fields.get("base_salary") or 0),
            float(fields.get("meal_days") or 0),
            float(fields.get("meal_daily") or 0),
            float(fields.get("meal_allowance") or 0),
            float(fields.get("holiday_subsidy") or 0),
            float(fields.get("christmas_subsidy") or 0),
            float(fields.get("other_earnings") or 0),
            float(fields.get("gross_taxable") or 0),
            float(fields.get("gross_exempt") or 0),
            float(fields.get("irs_rate") or 0),
            float(fields.get("irs_amount") or 0),
            float(fields.get("ss_rate") or 0),
            float(fields.get("ss_amount") or 0),
            float(fields.get("other_deductions") or 0),
            float(fields.get("total_deductions") or 0),
            float(fields.get("net_cash") or 0),
            float(fields.get("net_meal") or 0),
            float(fields.get("total_net") or 0),
            fields.get("iban"),
            float(fields.get("absence_hours") or 0),
            float(fields.get("accumulated_pay") or 0),
            float(fields.get("accumulated_irs") or 0),
            file_name,
            file_path,
            raw_text,
            confidence,
            json.dumps(warnings, ensure_ascii=False),
        ),
    )
    payslip_id = int(cur.lastrowid)
    pay_date = fields.get("pay_date") or month_bounds(month)[1]
    month_text = month_label(month)

    net_cash = float(fields.get("net_cash") or 0)
    if net_cash:
        account = get_account(conn, "conta-ordem")
        category = get_category(conn, "salario")
        if account:
            conn.execute(
                """INSERT INTO transactions (date, amount, type, account_id, category_id, description, payslip_id)
                   VALUES (?, ?, 'income', ?, ?, ?, ?)""",
                (
                    pay_date,
                    net_cash,
                    account["id"],
                    category["id"] if category else None,
                    f"Salário {month_text} — líquido",
                    payslip_id,
                ),
            )

    net_meal = float(fields.get("net_meal") or 0)
    if net_meal:
        account = get_account(conn, "cartao-refeicao")
        category = get_category(conn, "subsidio-alimentacao")
        if account:
            conn.execute(
                """INSERT INTO transactions (date, amount, type, account_id, category_id, description, payslip_id)
                   VALUES (?, ?, 'income', ?, ?, ?, ?)""",
                (
                    pay_date,
                    net_meal,
                    account["id"],
                    category["id"] if category else None,
                    f"Cartão refeição {month_text}",
                    payslip_id,
                ),
            )
    return payslip_id


def delete_payslip(conn: sqlite3.Connection, payslip_id: int) -> None:
    conn.execute("DELETE FROM transactions WHERE payslip_id = ?", (payslip_id,))
    conn.execute("DELETE FROM payslips WHERE id = ?", (payslip_id,))


def add_subscription(
    conn: sqlite3.Connection,
    name: str,
    amount: float,
    cadence: str,
    next_charge: str | None,
    category_id: int | None,
    account_id: int | None,
    url: str | None,
    notes: str | None,
) -> int:
    cur = conn.execute(
        """INSERT INTO subscriptions (name, amount, cadence, next_charge, category_id, account_id, url, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, amount, cadence, next_charge or None, category_id, account_id, url or None, notes or None),
    )
    return int(cur.lastrowid)


def pay_subscription(conn: sqlite3.Connection, subscription_id: int) -> None:
    row = conn.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,)).fetchone()
    if not row:
        return
    account = conn.execute(
        "SELECT * FROM accounts WHERE id = ?", (row["account_id"],)
    ).fetchone()
    if not account:
        account = get_account(conn, "conta-ordem")
    if not account:
        return
    category_id = row["category_id"]
    if not category_id:
        cat = get_category(conn, "subscricoes")
        category_id = cat["id"] if cat else None
    conn.execute(
        """INSERT INTO transactions (date, amount, type, account_id, category_id, description, subscription_id)
           VALUES (?, ?, 'expense', ?, ?, ?, ?)""",
        (
            today_iso(),
            -abs(float(row["amount"])),
            account["id"],
            category_id,
            f"{row['name']} — subscrição",
            row["id"],
        ),
    )
    conn.execute(
        "UPDATE subscriptions SET next_charge = ? WHERE id = ?",
        (add_cadence(row["next_charge"] or today_iso(), row["cadence"]), row["id"]),
    )


def contribute_goal(
    conn: sqlite3.Connection, goal_id: int, amount: float, account_id: int | None = None
) -> None:
    row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if not row or amount <= 0:
        return
    account = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone() if account_id else None
    if not account:
        account = get_account(conn, "conta-ordem")
    if not account:
        return
    slug = {
        "emergency": "fundo-emergencia",
        "investment": "investimentos-cat",
    }.get(row["kind"], "objetivos")
    category = get_category(conn, slug)
    conn.execute(
        """INSERT INTO transactions (date, amount, type, account_id, category_id, description, goal_id)
           VALUES (?, ?, 'expense', ?, ?, ?, ?)""",
        (
            today_iso(),
            -abs(amount),
            account["id"],
            category["id"] if category else None,
            f"Objetivo: {row['name']}",
            row["id"],
        ),
    )
    conn.execute(
        "UPDATE goals SET current = current + ? WHERE id = ?", (abs(amount), goal_id)
    )
