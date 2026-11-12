"""raiz — a small, local-first personal finance app.

Run with:
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import csv
import io
import json
import re
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Iterator
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import (
    DATA_DIR,
    DB_PATH,
    PAYSLIP_DIR,
    all_accounts,
    all_categories,
    get_db,
    get_setting,
    get_settings,
    init_db,
    latest_month,
    set_setting,
)
from app.icons import ICONS
from app.parsers.payslip import PayslipParseResult, parse_payslip
from app.services import (
    account_balances,
    add_subscription,
    allocation,
    budget_data,
    cashflow_series,
    category_totals,
    contribute_goal,
    create_payslip_and_transactions,
    dashboard_insights,
    delete_payslip,
    goals_data,
    month_summary,
    month_transactions,
    pay_subscription,
    payslip_data,
    payslips_list,
    subscriptions_data,
)
from app.utils import (
    PT_MONTHS,
    PT_MONTHS_SHORT,
    add_cadence,
    cadence_label,
    clamp,
    conic_gradient,
    current_month,
    date_full,
    date_label,
    days_until,
    eur,
    eur_compact,
    group_label,
    initials,
    month_add,
    month_bounds,
    month_display,
    month_label,
    month_short,
    monthly_equivalent,
    parse_int,
    parse_money,
    pct,
    pct_pt,
    slugify,
    today_iso,
    weekday_label,
)

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="raiz", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals.update(
    ICONS=ICONS,
    PT_MONTHS=PT_MONTHS,
    PT_MONTHS_SHORT=PT_MONTHS_SHORT,
    eur=eur,
    eur_compact=eur_compact,
    month_label=month_label,
    month_display=month_display,
    month_short=month_short,
    group_label=group_label,
    initials=initials,
    cadence_label=cadence_label,
    date_label=date_label,
    date_full=date_full,
    weekday_label=weekday_label,
    days_until=days_until,
    pct=pct,
    pct_pt=pct_pt,
    clamp=clamp,
    conic_gradient=conic_gradient,
    current_month=current_month,
    today_iso=today_iso,
    monthly_equivalent=monthly_equivalent,
    zip=zip,
    min=min,
    max=max,
    len=len,
    round=round,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def redirect(path: str, msg: str | None = None, error: str | None = None) -> RedirectResponse:
    params = {}
    if msg:
        params["msg"] = msg
    if error:
        params["error"] = error
    url = f"{path}?{urlencode(params)}" if params else path
    return RedirectResponse(url, status_code=303)


def db_session() -> Iterator[sqlite3.Connection]:
    with get_db() as conn:
        yield conn


def render(
    request: Request,
    conn: sqlite3.Connection,
    template: str,
    page: str,
    **context: Any,
) -> HTMLResponse:
    settings = get_settings(conn)
    base: dict[str, Any] = {
        "request": request,
        "page": page,
        "settings": settings,
        "profile_name": settings.get("profile_name", ""),
        "flash": request.query_params.get("msg"),
        "flash_error": request.query_params.get("error"),
        "current_month_value": current_month(),
        "today_value": today_iso(),
        "months": month_options(conn),
        "allocation_settings": allocation(conn),
    }
    base.update(context)
    return templates.TemplateResponse(request=request, name=template, context=base)


def month_options(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """SELECT month FROM payslips
           UNION
           SELECT substr(date, 1, 7) FROM transactions"""
    ).fetchall()
    months = sorted({row[0] for row in rows if row[0]}, reverse=True)
    if current_month() not in months:
        months.insert(0, current_month())
    return months[:24]


def resolve_month(conn: sqlite3.Connection, requested: str | None) -> str:
    if requested and re.fullmatch(r"\d{4}-\d{2}", requested):
        return requested
    return latest_month(conn) or current_month()


def shift_month(month: str, delta: int) -> str:
    return month_add(month, delta)


def group_by_date(items: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for item in items:
        if not groups or groups[-1][0] != item["date"]:
            groups.append((item["date"], [item]))
        else:
            groups[-1][1].append(item)
    return groups


def greeting() -> str:
    hour = datetime.now().hour
    if hour < 6:
        return "Boa noite"
    if hour < 12:
        return "Bom dia"
    if hour < 19:
        return "Boa tarde"
    return "Boa noite"


def payslip_month(conn: sqlite3.Connection, month: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id FROM payslips WHERE month = ? ORDER BY id DESC LIMIT 1", (month,)
    ).fetchone()
    return payslip_data(conn, row["id"]) if row else None


def safe_import_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-f0-9]{32}", value))


def is_within(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def stored_path(value: str | None) -> Path | None:
    """Accept both old absolute paths and new paths relative to data/."""
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else DATA_DIR / path


def form_money(form: dict[str, Any], key: str, default: float = 0.0) -> float:
    return parse_money(form.get(key), default)


def form_text(form: dict[str, Any], key: str) -> str | None:
    value = form.get(key)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def payslip_fields_from_form(form: dict[str, Any]) -> dict[str, Any]:
    money_keys = [
        "base_salary",
        "meal_days",
        "meal_daily",
        "meal_allowance",
        "holiday_subsidy",
        "christmas_subsidy",
        "other_earnings",
        "gross_taxable",
        "gross_exempt",
        "irs_rate",
        "irs_amount",
        "ss_rate",
        "ss_amount",
        "other_deductions",
        "total_deductions",
        "net_cash",
        "net_meal",
        "total_net",
        "absence_hours",
        "accumulated_pay",
        "accumulated_irs",
    ]
    fields: dict[str, Any] = {}
    for key in money_keys:
        fields[key] = form_money(form, key)
    for key in [
        "month",
        "employer",
        "employee_name",
        "employee_nif",
        "employee_ss",
        "employee_number",
        "professional_category",
        "work_location",
        "pay_date",
        "iban",
    ]:
        fields[key] = form_text(form, key)
    if not fields["total_deductions"]:
        fields["total_deductions"] = round(
            fields["irs_amount"] + fields["ss_amount"] + fields["other_deductions"], 2
        )
    if not fields.get("gross_exempt"):
        fields["gross_exempt"] = fields["meal_allowance"]
    if not fields["total_net"]:
        fields["total_net"] = round(fields["net_cash"] + fields["net_meal"], 2)
    return fields


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
    month: str | None = None,
) -> HTMLResponse:
    selected = resolve_month(conn, month)
    summary = month_summary(conn, selected)
    transactions = month_transactions(conn, selected, limit=7)
    categories = category_totals(conn, selected)
    cashflow = cashflow_series(conn, selected, 6)
    subscriptions = subscriptions_data(conn)
    goals = goals_data(conn)
    payslip = payslip_month(conn, selected)
    alloc = allocation(conn)
    allocation_amounts = {
        key: round(summary["income"] * value / 100, 2) for key, value in alloc.items()
    }
    insights = dashboard_insights(conn, selected, summary, subscriptions, goals, payslip)
    return render(
        request,
        conn,
        "dashboard.html",
        "dashboard",
        selected_month=selected,
        month_title=month_display(selected),
        prev_month=shift_month(selected, -1),
        next_month=shift_month(selected, 1),
        greeting=greeting(),
        summary=summary,
        transactions=transactions,
        category_totals=categories,
        category_gradient=conic_gradient(categories),
        cashflow=cashflow,
        subscriptions=subscriptions,
        goals=goals,
        payslip=payslip,
        allocation=alloc,
        allocation_amounts=allocation_amounts,
        insights=insights,
        is_current=selected == current_month(),
    )


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

@app.get("/transactions", response_class=HTMLResponse)
def transactions_page(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
    month: str | None = None,
    type: str = "",
    account: str = "",
    category: str = "",
    q: str = "",
) -> HTMLResponse:
    selected = resolve_month(conn, month)
    account_id = parse_int(account) or None
    category_id = parse_int(category) or None
    items = month_transactions(
        conn,
        selected,
        type_filter=type,
        account_id=account_id,
        category_id=category_id,
        search=q.strip(),
    )
    summary = month_summary(conn, selected)
    return render(
        request,
        conn,
        "transactions.html",
        "transactions",
        selected_month=selected,
        month_title=month_display(selected),
        prev_month=shift_month(selected, -1),
        next_month=shift_month(selected, 1),
        transactions=items,
        transaction_groups=group_by_date(items),
        summary=summary,
        accounts=all_accounts(conn),
        categories=all_categories(conn, include_income=True),
        filter_type=type,
        filter_account=account,
        filter_category=category,
        filter_q=q,
    )


@app.post("/transactions")
async def create_transaction(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    form = await request.form()
    month = str(form.get("month") or current_month())
    try:
        amount = abs(parse_money(form.get("amount")))
        type_ = str(form.get("type") or "expense")
        if type_ not in {"income", "expense"}:
            type_ = "expense"
        if amount <= 0:
            return redirect(f"/transactions?month={month}", error="Indica um valor maior do que zero.")
        date_value = str(form.get("date") or today_iso())[:10]
        description = str(form.get("description") or "").strip()
        if not description:
            return redirect(f"/transactions?month={month}", error="Dá um nome ao movimento.")
        account_id = parse_int(form.get("account_id"))
        category_id = parse_int(form.get("category_id")) or None
        if not account_id:
            return redirect(f"/transactions?month={month}", error="Escolhe uma conta.")
        signed = amount if type_ == "income" else -amount
        conn.execute(
            """INSERT INTO transactions (date, amount, type, account_id, category_id, description, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                date_value,
                signed,
                type_,
                account_id,
                category_id,
                description,
                str(form.get("notes") or "").strip() or None,
            ),
        )
        return redirect(f"/transactions?month={date_value[:7]}", msg="Movimento adicionado.")
    except Exception as exc:
        return redirect(f"/transactions?month={month}", error=f"Não consegui guardar: {exc}")


@app.post("/transactions/{transaction_id}")
async def update_transaction(
    transaction_id: int,
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    form = await request.form()
    month = str(form.get("month") or current_month())
    try:
        amount = abs(parse_money(form.get("amount")))
        type_ = str(form.get("type") or "expense")
        if type_ not in {"income", "expense"}:
            type_ = "expense"
        if amount <= 0:
            return redirect(f"/transactions?month={month}", error="Indica um valor maior do que zero.")
        date_value = str(form.get("date") or today_iso())[:10]
        description = str(form.get("description") or "").strip()
        if not description:
            return redirect(f"/transactions?month={month}", error="Dá um nome ao movimento.")
        signed = amount if type_ == "income" else -amount
        conn.execute(
            """UPDATE transactions
               SET date = ?, amount = ?, type = ?, account_id = ?, category_id = ?, description = ?, notes = ?
               WHERE id = ?""",
            (
                date_value,
                signed,
                type_,
                parse_int(form.get("account_id")),
                parse_int(form.get("category_id")) or None,
                description,
                str(form.get("notes") or "").strip() or None,
                transaction_id,
            ),
        )
        return redirect(f"/transactions?month={date_value[:7]}", msg="Movimento atualizado.")
    except Exception as exc:
        return redirect(f"/transactions?month={month}", error=f"Não consegui atualizar: {exc}")


@app.post("/transactions/{transaction_id}/delete")
def remove_transaction(
    transaction_id: int,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
    month: str | None = None,
) -> RedirectResponse:
    conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
    return redirect(f"/transactions?month={month or current_month()}", msg="Movimento apagado.")


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

@app.get("/budget", response_class=HTMLResponse)
def budget_page(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
    month: str | None = None,
) -> HTMLResponse:
    selected = resolve_month(conn, month)
    data = budget_data(conn, selected)
    return render(
        request,
        conn,
        "budget.html",
        "budget",
        selected_month=selected,
        month_title=month_display(selected),
        prev_month=shift_month(selected, -1),
        next_month=shift_month(selected, 1),
        budget=data,
        allocation=allocation(conn),
        summary=month_summary(conn, selected),
    )


@app.post("/budget")
async def save_budget(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    form = await request.form()
    month = str(form.get("month") or current_month())
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        return redirect("/budget", error="Mês inválido.")
    for key, value in form.items():
        if not key.startswith("planned_"):
            continue
        try:
            category_id = int(key.removeprefix("planned_"))
        except ValueError:
            continue
        planned = max(0.0, parse_money(value))
        conn.execute(
            """INSERT INTO budgets (month, category_id, planned) VALUES (?, ?, ?)
               ON CONFLICT(month, category_id) DO UPDATE SET planned = excluded.planned""",
            (month, category_id, planned),
        )
    return redirect(f"/budget?month={month}", msg="Orçamento guardado.")


@app.post("/budget/suggest")
async def suggest_budget(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    form = await request.form()
    month = str(form.get("month") or current_month())
    summary = month_summary(conn, month)
    alloc = allocation(conn)
    data = budget_data(conn, month)
    if summary["income"] <= 0:
        return redirect(f"/budget?month={month}", error="Ainda não há rendimento neste mês para distribuir.")
    for group in data["groups"]:
        group_target = summary["income"] * alloc.get(group["key"], 0) / 100
        categories = group["categories"]
        if not categories:
            continue
        spent_total = sum(max(0.0, c["spent"]) for c in categories)
        remaining = group_target
        for index, cat in enumerate(categories):
            if index == len(categories) - 1:
                share = remaining
            elif spent_total > 0:
                share = round(group_target * (max(0.0, cat["spent"]) / spent_total), 2)
            else:
                share = round(group_target / len(categories), 2)
            share = max(0.0, share)
            remaining = round(remaining - share, 2)
            conn.execute(
                """INSERT INTO budgets (month, category_id, planned) VALUES (?, ?, ?)
                   ON CONFLICT(month, category_id) DO UPDATE SET planned = excluded.planned""",
                (month, cat["id"], share),
            )
    return redirect(
        f"/budget?month={month}",
        msg="Distribuí o rendimento pela regra de alocação. Ajusta os valores como quiseres.",
    )


# ---------------------------------------------------------------------------
# Payslips
# ---------------------------------------------------------------------------

@app.get("/payslips", response_class=HTMLResponse)
def payslips_page(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> HTMLResponse:
    items = payslips_list(conn)
    ytd = [p for p in items if p["month"].startswith(str(date.today().year))]
    return render(
        request,
        conn,
        "payslips.html",
        "payslips",
        payslips=items,
        ytd_net=sum(float(p["total_net"] or 0) for p in ytd),
        ytd_irs=sum(float(p["irs_amount"] or 0) for p in ytd),
        ytd_ss=sum(float(p["ss_amount"] or 0) for p in ytd),
        ytd_meal=sum(float(p["net_meal"] or 0) for p in ytd),
        latest=items[0] if items else None,
    )


@app.get("/payslips/import", response_class=HTMLResponse)
def payslip_import_page(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> HTMLResponse:
    return render(request, conn, "payslip_import.html", "payslips")


@app.post("/payslips/import")
async def upload_payslip(
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
    file: UploadFile = File(...),
) -> RedirectResponse:
    filename = file.filename or "recibo.pdf"
    if not filename.lower().endswith(".pdf"):
        return redirect("/payslips/import", error="O recibo tem de ser um PDF.")
    content = await file.read()
    if len(content) > 15 * 1024 * 1024:
        return redirect("/payslips/import", error="O PDF é demasiado grande (máx. 15 MB).")
    if not content.startswith(b"%PDF"):
        return redirect("/payslips/import", error="O ficheiro não parece ser um PDF válido.")
    import_id = uuid4().hex
    path = PAYSLIP_DIR / f"{import_id}.pdf"
    path.write_bytes(content)
    try:
        parsed = parse_payslip(path)
    except Exception as exc:
        path.unlink(missing_ok=True)
        return redirect("/payslips/import", error=f"Não consegui ler o PDF: {exc}")
    if not parsed.raw_text.strip():
        path.unlink(missing_ok=True)
        return redirect(
            "/payslips/import",
            error="O PDF não tem texto selecionável (pode ser uma imagem digitalizada). Neste caso, cria o recibo manualmente.",
        )
    conn.execute(
        """INSERT INTO payslip_imports (id, file_name, file_path, raw_text, parsed_json)
           VALUES (?, ?, ?, ?, ?)""",
        (
            import_id,
            filename,
            str(path.relative_to(DATA_DIR)),
            parsed.raw_text,
            json.dumps(parsed.to_dict(), ensure_ascii=False),
        ),
    )
    return redirect(f"/payslips/import/{import_id}")


@app.get("/payslips/import/{import_id}", response_class=HTMLResponse)
def payslip_review_page(
    import_id: str,
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> HTMLResponse:
    if not safe_import_id(import_id):
        return redirect("/payslips", error="Importação inválida.")
    row = conn.execute("SELECT * FROM payslip_imports WHERE id = ?", (import_id,)).fetchone()
    if not row:
        return redirect("/payslips", error="Esta importação já não existe.")
    data = json.loads(row["parsed_json"])
    fields = data.get("fields", {})
    duplicate = None
    if fields.get("month") and fields.get("employer"):
        duplicate = conn.execute(
            "SELECT id FROM payslips WHERE month = ? AND employer = ?",
            (fields["month"], fields["employer"]),
        ).fetchone()
    return render(
        request,
        conn,
        "payslip_review.html",
        "payslips",
        import_id=import_id,
        import_row=dict(row),
        fields=fields,
        warnings=data.get("warnings", []),
        confidence=data.get("confidence", 0),
        duplicate=dict(duplicate) if duplicate else None,
        raw_excerpt="\n".join(row["raw_text"].splitlines()[:18]),
    )


@app.post("/payslips/import/{import_id}")
async def confirm_payslip(
    import_id: str,
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    if not safe_import_id(import_id):
        return redirect("/payslips", error="Importação inválida.")
    row = conn.execute("SELECT * FROM payslip_imports WHERE id = ?", (import_id,)).fetchone()
    if not row:
        return redirect("/payslips", error="Esta importação já não existe.")
    form = await request.form()
    fields = payslip_fields_from_form(dict(form))
    if not fields.get("month") or not re.fullmatch(r"\d{4}-\d{2}", fields["month"]):
        return redirect(f"/payslips/import/{import_id}", error="Indica o mês do recibo.")
    if not fields.get("employer"):
        fields["employer"] = "Sem empregador"
    duplicate = conn.execute(
        "SELECT id FROM payslips WHERE month = ? AND employer = ?",
        (fields["month"], fields["employer"]),
    ).fetchone()
    if duplicate:
        import_data = json.loads(row["parsed_json"])
        import_data["fields"] = fields
        conn.execute(
            "UPDATE payslip_imports SET parsed_json = ? WHERE id = ?",
            (json.dumps(import_data, ensure_ascii=False), import_id),
        )
        return redirect(
            f"/payslips/import/{import_id}",
            error="Já existe um recibo para este mês e empregador. Apaga o antigo primeiro, se quiseres substituir.",
        )
    if not fields.get("pay_date"):
        fields["pay_date"] = month_bounds(fields["month"])[1]
    try:
        payslip_id = create_payslip_and_transactions(
            conn,
            fields,
            row["file_name"],
            row["file_path"],
            row["raw_text"],
            float(json.loads(row["parsed_json"]).get("confidence", 0)),
            json.loads(row["parsed_json"]).get("warnings", []),
        )
    except sqlite3.IntegrityError:
        import_data = json.loads(row["parsed_json"])
        import_data["fields"] = fields
        conn.execute(
            "UPDATE payslip_imports SET parsed_json = ? WHERE id = ?",
            (json.dumps(import_data, ensure_ascii=False), import_id),
        )
        return redirect(
            f"/payslips/import/{import_id}",
            error="Já existe um recibo para este mês e empregador.",
        )
    conn.execute("DELETE FROM payslip_imports WHERE id = ?", (import_id,))
    return redirect(f"/payslips/{payslip_id}", msg="Recibo importado e lançado nos movimentos.")


@app.post("/payslips/import/{import_id}/cancel")
def cancel_payslip_import(
    import_id: str,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    if safe_import_id(import_id):
        row = conn.execute("SELECT * FROM payslip_imports WHERE id = ?", (import_id,)).fetchone()
        if row:
            path = stored_path(row["file_path"])
            if path and is_within(path, PAYSLIP_DIR):
                path.unlink(missing_ok=True)
            conn.execute("DELETE FROM payslip_imports WHERE id = ?", (import_id,))
    return redirect("/payslips", msg="Importação cancelada.")


@app.get("/payslips/{payslip_id}", response_class=HTMLResponse)
def payslip_detail(
    payslip_id: int,
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> HTMLResponse:
    item = payslip_data(conn, payslip_id)
    if not item:
        return redirect("/payslips", error="Recibo não encontrado.")
    return render(
        request,
        conn,
        "payslip_detail.html",
        "payslips",
        payslip=item,
        warnings=json.loads(item.get("warnings_json") or "[]"),
    )


@app.get("/payslips/{payslip_id}/file")
def payslip_file(
    payslip_id: int,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> Response:
    row = conn.execute("SELECT file_path, file_name FROM payslips WHERE id = ?", (payslip_id,)).fetchone()
    if not row or not row["file_path"]:
        return Response(status_code=404)
    path = stored_path(row["file_path"])
    if not path or not path.exists() or not is_within(path, PAYSLIP_DIR):
        return Response(status_code=404)
    return FileResponse(path, media_type="application/pdf", filename=row["file_name"] or path.name)


@app.post("/payslips/{payslip_id}/delete")
def remove_payslip(
    payslip_id: int,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    row = conn.execute("SELECT file_path FROM payslips WHERE id = ?", (payslip_id,)).fetchone()
    if row and row["file_path"]:
        path = stored_path(row["file_path"])
        if path and is_within(path, PAYSLIP_DIR):
            path.unlink(missing_ok=True)
    delete_payslip(conn, payslip_id)
    return redirect("/payslips", msg="Recibo e movimentos associados apagados.")


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

@app.get("/subscriptions", response_class=HTMLResponse)
def subscriptions_page(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> HTMLResponse:
    return render(
        request,
        conn,
        "subscriptions.html",
        "subscriptions",
        subscriptions=subscriptions_data(conn),
        accounts=all_accounts(conn),
        categories=all_categories(conn),
    )


@app.post("/subscriptions")
async def create_subscription(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    form = await request.form()
    name = str(form.get("name") or "").strip()
    amount = abs(parse_money(form.get("amount")))
    if not name or amount <= 0:
        return redirect("/subscriptions", error="Preenche o nome e um valor válido.")
    cadence = str(form.get("cadence") or "monthly")
    if cadence not in {"weekly", "monthly", "quarterly", "yearly"}:
        cadence = "monthly"
    add_subscription(
        conn,
        name,
        amount,
        cadence,
        str(form.get("next_charge") or "") or None,
        parse_int(form.get("category_id")) or None,
        parse_int(form.get("account_id")) or None,
        str(form.get("url") or "").strip() or None,
        str(form.get("notes") or "").strip() or None,
    )
    return redirect("/subscriptions", msg="Subscrição adicionada.")


@app.post("/subscriptions/{subscription_id}")
async def update_subscription(
    subscription_id: int,
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    form = await request.form()
    name = str(form.get("name") or "").strip()
    amount = abs(parse_money(form.get("amount")))
    if not name or amount <= 0:
        return redirect("/subscriptions", error="Preenche o nome e um valor válido.")
    cadence = str(form.get("cadence") or "monthly")
    if cadence not in {"weekly", "monthly", "quarterly", "yearly"}:
        cadence = "monthly"
    conn.execute(
        """UPDATE subscriptions
           SET name = ?, amount = ?, cadence = ?, next_charge = ?, category_id = ?, account_id = ?, url = ?, notes = ?
           WHERE id = ?""",
        (
            name,
            amount,
            cadence,
            str(form.get("next_charge") or "") or None,
            parse_int(form.get("category_id")) or None,
            parse_int(form.get("account_id")) or None,
            str(form.get("url") or "").strip() or None,
            str(form.get("notes") or "").strip() or None,
            subscription_id,
        ),
    )
    return redirect("/subscriptions", msg="Subscrição atualizada.")


@app.post("/subscriptions/{subscription_id}/pay")
def mark_subscription_paid(
    subscription_id: int,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    pay_subscription(conn, subscription_id)
    return redirect("/subscriptions", msg="Subscrição marcada como paga e próximo débito atualizado.")


@app.post("/subscriptions/{subscription_id}/delete")
def remove_subscription(
    subscription_id: int,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    conn.execute("DELETE FROM subscriptions WHERE id = ?", (subscription_id,))
    return redirect("/subscriptions", msg="Subscrição apagada.")


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

@app.get("/goals", response_class=HTMLResponse)
def goals_page(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> HTMLResponse:
    return render(
        request,
        conn,
        "goals.html",
        "goals",
        goals=goals_data(conn),
        accounts=all_accounts(conn),
    )


@app.post("/goals")
async def create_goal(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    form = await request.form()
    name = str(form.get("name") or "").strip()
    target = abs(parse_money(form.get("target")))
    if not name or target <= 0:
        return redirect("/goals", error="Dá um nome ao objetivo e define um valor a atingir.")
    kind = str(form.get("kind") or "saving")
    if kind not in {"emergency", "saving", "investment", "purchase"}:
        kind = "saving"
    current = max(0.0, parse_money(form.get("current")))
    monthly = max(0.0, parse_money(form.get("monthly_contribution")))
    conn.execute(
        """INSERT INTO goals (name, target, current, monthly_contribution, deadline, kind, color, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            name,
            target,
            current,
            monthly,
            str(form.get("deadline") or "") or None,
            kind,
            str(form.get("color") or "#2F5D46"),
            str(form.get("notes") or "").strip() or None,
        ),
    )
    return redirect("/goals", msg="Objetivo criado.")


@app.post("/goals/{goal_id}")
async def update_goal(
    goal_id: int,
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    form = await request.form()
    name = str(form.get("name") or "").strip()
    target = abs(parse_money(form.get("target")))
    if not name or target <= 0:
        return redirect("/goals", error="Dá um nome ao objetivo e define um valor a atingir.")
    kind = str(form.get("kind") or "saving")
    if kind not in {"emergency", "saving", "investment", "purchase"}:
        kind = "saving"
    conn.execute(
        """UPDATE goals
           SET name = ?, target = ?, current = ?, monthly_contribution = ?, deadline = ?, kind = ?, color = ?, notes = ?
           WHERE id = ?""",
        (
            name,
            target,
            max(0.0, parse_money(form.get("current"))),
            max(0.0, parse_money(form.get("monthly_contribution"))),
            str(form.get("deadline") or "") or None,
            kind,
            str(form.get("color") or "#2F5D46"),
            str(form.get("notes") or "").strip() or None,
            goal_id,
        ),
    )
    return redirect("/goals", msg="Objetivo atualizado.")


@app.post("/goals/{goal_id}/contribute")
async def add_to_goal(
    goal_id: int,
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    form = await request.form()
    amount = abs(parse_money(form.get("amount")))
    if amount <= 0:
        return redirect("/goals", error="Indica um valor para reforçar o objetivo.")
    contribute_goal(conn, goal_id, amount, parse_int(form.get("account_id")) or None)
    return redirect("/goals", msg="Reforço registado e adicionado aos movimentos.")


@app.post("/goals/{goal_id}/delete")
def remove_goal(
    goal_id: int,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
    return redirect("/goals", msg="Objetivo apagado.")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> HTMLResponse:
    counts = {
        "transactions": conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
        "payslips": conn.execute("SELECT COUNT(*) FROM payslips").fetchone()[0],
        "subscriptions": conn.execute("SELECT COUNT(*) FROM subscriptions WHERE active = 1").fetchone()[0],
        "goals": conn.execute("SELECT COUNT(*) FROM goals WHERE archived = 0").fetchone()[0],
    }
    db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    return render(
        request,
        conn,
        "settings.html",
        "settings",
        accounts=all_accounts(conn),
        balances=account_balances(conn),
        categories=all_categories(conn, include_income=True),
        counts=counts,
        db_size=db_size,
        data_path=str(DATA_DIR),
        allocation=allocation(conn),
    )


@app.post("/settings/profile")
async def save_profile(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    form = await request.form()
    name = str(form.get("profile_name") or "").strip() or "Guilherme"
    essentials = max(0, parse_int(form.get("essentials")))
    lifestyle = max(0, parse_int(form.get("lifestyle")))
    future = max(0, parse_int(form.get("future")))
    if essentials + lifestyle + future != 100:
        return redirect("/settings", error="A alocação tem de somar 100%.")
    set_setting(conn, "profile_name", name)
    set_setting(
        conn,
        "allocation",
        json.dumps({"essentials": essentials, "lifestyle": lifestyle, "future": future}),
    )
    set_setting(conn, "emergency_months", str(max(1, parse_int(form.get("emergency_months"), 6))))
    return redirect("/settings", msg="Definições guardadas.")


@app.post("/settings/accounts")
async def create_account(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    form = await request.form()
    name = str(form.get("name") or "").strip()
    type_ = str(form.get("type") or "checking")
    if not name:
        return redirect("/settings", error="Dá um nome à conta.")
    if type_ not in {"checking", "savings", "meal", "investment", "cash", "credit"}:
        type_ = "checking"
    slug = slugify(name)
    suffix = 2
    original = slug
    while conn.execute("SELECT 1 FROM accounts WHERE slug = ?", (slug,)).fetchone():
        slug = f"{original}-{suffix}"
        suffix += 1
    conn.execute(
        "INSERT INTO accounts (slug, name, type, institution, opening_balance, color) VALUES (?, ?, ?, ?, ?, ?)",
        (
            slug,
            name,
            type_,
            str(form.get("institution") or "").strip() or None,
            parse_money(form.get("opening_balance")),
            str(form.get("color") or "#2F5D46"),
        ),
    )
    return redirect("/settings", msg="Conta adicionada.")


@app.post("/settings/accounts/{account_id}/delete")
def archive_account(
    account_id: int,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    row = conn.execute("SELECT slug FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row and row["slug"] in {"conta-ordem", "cartao-refeicao"}:
        return redirect("/settings", error="Esta conta é essencial para importar recibos. Não pode ser apagada.")
    used = conn.execute("SELECT COUNT(*) FROM transactions WHERE account_id = ?", (account_id,)).fetchone()[0]
    if used:
        conn.execute("UPDATE accounts SET archived = 1 WHERE id = ?", (account_id,))
        return redirect("/settings", msg="Conta arquivada (tinha movimentos associados).")
    conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
    return redirect("/settings", msg="Conta apagada.")


@app.post("/settings/categories")
async def create_category(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    form = await request.form()
    name = str(form.get("name") or "").strip()
    group = str(form.get("group_name") or "other")
    kind = str(form.get("kind") or "expense")
    if not name:
        return redirect("/settings", error="Dá um nome à categoria.")
    if group not in {"essentials", "lifestyle", "future", "other"}:
        group = "other"
    if kind not in {"expense", "saving", "investment"}:
        kind = "expense"
    slug = slugify(name)
    suffix = 2
    original = slug
    while conn.execute("SELECT 1 FROM categories WHERE slug = ?", (slug,)).fetchone():
        slug = f"{original}-{suffix}"
        suffix += 1
    max_order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) FROM categories").fetchone()[0]
    conn.execute(
        """INSERT INTO categories (slug, name, kind, group_name, icon, color, sort_order)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            slug,
            name,
            kind,
            group,
            str(form.get("icon") or "more"),
            str(form.get("color") or "#8C7A5E"),
            int(max_order) + 1,
        ),
    )
    return redirect("/settings", msg="Categoria adicionada.")


@app.post("/settings/categories/{category_id}/delete")
def archive_category(
    category_id: int,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    used = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE category_id = ?", (category_id,)
    ).fetchone()[0]
    budgeted = conn.execute(
        "SELECT COUNT(*) FROM budgets WHERE category_id = ?", (category_id,)
    ).fetchone()[0]
    if used or budgeted:
        conn.execute("UPDATE categories SET archived = 1 WHERE id = ?", (category_id,))
        return redirect("/settings", msg="Categoria arquivada (tinha movimentos ou orçamento).")
    conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    return redirect("/settings", msg="Categoria apagada.")


@app.post("/settings/reset")
async def reset_all_data(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(db_session)],
) -> RedirectResponse:
    form = await request.form()
    if str(form.get("confirm") or "").strip().upper() != "APAGAR":
        return redirect("/settings", error="Escreve APAGAR para confirmar.")
    for table in ("transactions", "budgets", "subscriptions", "goals", "payslips", "payslip_imports"):
        conn.execute(f"DELETE FROM {table}")
    for path in PAYSLIP_DIR.glob("*.pdf"):
        path.unlink(missing_ok=True)
    return redirect("/", msg="Dados apagados. As contas, categorias e definições mantêm-se.")


# ---------------------------------------------------------------------------
# Export and backup
# ---------------------------------------------------------------------------

@app.get("/export/json")
def export_json(conn: Annotated[sqlite3.Connection, Depends(db_session)]) -> Response:
    payload: dict[str, Any] = {}
    for table in (
        "accounts",
        "categories",
        "payslips",
        "transactions",
        "subscriptions",
        "goals",
        "budgets",
        "settings",
    ):
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        payload[table] = [dict(row) for row in rows]
    payload["exported_at"] = datetime.now().isoformat(timespec="seconds")
    body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="raiz-{date.today().isoformat()}.json"'
        },
    )


@app.get("/export/csv")
def export_csv(conn: Annotated[sqlite3.Connection, Depends(db_session)]) -> Response:
    rows = conn.execute(
        """SELECT t.date, t.type, t.description, c.name AS category, a.name AS account, t.amount, t.notes
           FROM transactions t
           LEFT JOIN categories c ON c.id = t.category_id
           LEFT JOIN accounts a ON a.id = t.account_id
           ORDER BY t.date, t.id"""
    ).fetchall()
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["data", "tipo", "descrição", "categoria", "conta", "montante", "notas"])
    for row in rows:
        writer.writerow([row["date"], row["type"], row["description"], row["category"], row["account"], row["amount"], row["notes"] or ""])
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="raiz-movimentos-{date.today().isoformat()}.csv"'
        },
    )


@app.get("/backup")
def backup(conn: Annotated[sqlite3.Connection, Depends(db_session)]) -> Response:
    buffer = io.BytesIO()
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_db = Path(temp_dir) / "raiz.db"
        target = sqlite3.connect(temp_db)
        try:
            conn.backup(target)
        finally:
            target.close()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(temp_db, "raiz.db")
            if PAYSLIP_DIR.exists():
                for pdf in PAYSLIP_DIR.glob("*.pdf"):
                    archive.write(pdf, f"payslips/{pdf.name}")
            archive.writestr(
                "README.txt",
                "Backup do raiz. Contém a base de dados SQLite e os recibos em PDF.\n"
                "Guarda este ficheiro num local seguro.\n",
            )
    buffer.seek(0)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="raiz-backup-{date.today().isoformat()}.zip"'
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/favicon.ico")
def favicon() -> Response:
    return RedirectResponse("/static/img/favicon.svg", status_code=307)


@app.get("/robots.txt")
def robots() -> Response:
    return Response("User-agent: *\nDisallow: /\n", media_type="text/plain")
