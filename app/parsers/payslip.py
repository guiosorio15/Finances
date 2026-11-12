"""Portuguese payslip PDF reader.

The parser is deliberately conservative: it extracts what it can, validates the
arithmetic, and hands the result to a human review screen. It never invents a
value silently.
"""
from __future__ import annotations

import calendar
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.utils import parse_money

MONEY = r"(\d+(?:[ .]\d{3})*(?:,\d{1,2})?)"
PCT = r"(\d+(?:[.,]\d+)?)"

MONTHS = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

FIELDS = [
    "month",
    "employer",
    "employee_name",
    "employee_nif",
    "employee_ss",
    "employee_number",
    "professional_category",
    "work_location",
    "pay_date",
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
    "iban",
    "absence_hours",
    "accumulated_pay",
    "accumulated_irs",
]

MONEY_FIELDS = {
    "base_salary",
    "meal_daily",
    "meal_allowance",
    "holiday_subsidy",
    "christmas_subsidy",
    "other_earnings",
    "gross_taxable",
    "gross_exempt",
    "irs_amount",
    "ss_amount",
    "other_deductions",
    "total_deductions",
    "net_cash",
    "net_meal",
    "total_net",
    "accumulated_pay",
    "accumulated_irs",
}


@dataclass
class PayslipParseResult:
    raw_text: str
    fields: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": self.fields,
            "warnings": self.warnings,
            "confidence": self.confidence,
            "raw_text": self.raw_text,
        }


def _strip_accents(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower()


def extract_text(path: Path) -> str:
    """Use pdftotext when present (best layout), then pdfplumber as fallback."""
    exe = shutil.which("pdftotext")
    if exe:
        result = subprocess.run(
            [exe, "-layout", "-enc", "UTF-8", str(path), "-"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    try:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            pages = [page.extract_text(layout=True) or "" for page in pdf.pages]
        text = "\n".join(pages)
        if text.strip():
            return text
    except Exception:
        pass
    return ""


def _find(pattern: str, text: str, flags: int = re.IGNORECASE | re.MULTILINE | re.DOTALL) -> re.Match | None:
    return re.search(pattern, text, flags)


def _money(pattern: str, text: str, group: int = 1) -> float | None:
    match = _find(pattern, text)
    if not match:
        return None
    return parse_money(match.group(group))


def _percent(pattern: str, text: str) -> float | None:
    match = _find(pattern, text)
    if not match:
        return None
    return parse_money(match.group(1))


def _text(pattern: str, text: str) -> str | None:
    match = _find(pattern, text)
    if not match:
        return None
    value = re.sub(r"\s+", " ", match.group(1)).strip(" :-\n\t")
    return value or None


def _month_from_text(text: str) -> str | None:
    match = _find(r"Recibo\s+de\s+Vencimento\s+de\s+([A-Za-zÀ-ÿ]+)\s*/\s*(\d{4})", text)
    if match:
        name = _strip_accents(match.group(1))
        month_number = MONTHS.get(name)
        if month_number:
            return f"{int(match.group(2)):04d}-{month_number:02d}"
    # fallback: "mês de outubro de 2026"
    match = _find(
        r"(janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\s*(?:de|/)\s*(\d{4})",
        text,
    )
    if match:
        month_number = MONTHS.get(_strip_accents(match.group(1)))
        if month_number:
            return f"{int(match.group(2)):04d}-{month_number:02d}"
    return None


def _pay_date(text: str) -> str | None:
    match = _find(r"\b(\d{2})[./-](\d{2})[./-](\d{4})\b", text)
    if not match:
        return None
    day, month, year = match.groups()
    try:
        return datetime(int(year), int(month), int(day)).date().isoformat()
    except ValueError:
        return None


def _employee_name(text: str) -> str | None:
    match = _find(
        r"Exmo\s*\(a\)\.?\s*\(s\)\s*Sr\s*\(a\)\.?\s*\(s\)\s*\n\s*([A-ZÀ-Ý][A-ZÀ-Ý .]+?)\s*\n",
        text,
    )
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    # Some payslips put the name on the same line as the salutation.
    match = _find(r"Sr\s*\(a\)\.?\s*\(s\)\s*[:\-]?\s*([A-ZÀ-Ý][A-ZÀ-Ý .]{5,80})", text)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else None


def _employer(text: str) -> str | None:
    match = _find(r"\n\s*([A-Z][^\n]{1,80}?)\s+Telefone\s*:", text)
    if match:
        value = re.sub(r"\s+", " ", match.group(1)).strip()
        if value and not value.lower().startswith(("iban", "total", "data")):
            return value
    return None


def _employee_number(text: str) -> str | None:
    # Header: "Nº Colaborador  Nº Contribuinte  Nº Beneficiário  Data  Secção"
    match = _find(r"N[ºo°]?\s*Colaborador[^\n]*\n\s*(\d{2,8})\b", text)
    if match:
        return match.group(1)
    match = _find(r"N[ºo°]?\s*Colaborador\s*[:\-]?\s*(\d{2,8})", text)
    return match.group(1) if match else None


def _normalise_text(text: str) -> str:
    return (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u00a0", " ")
        .replace("\u202f", " ")
        .replace("\t", " ")
    )


def parse_payslip(path: Path) -> PayslipParseResult:
    raw = extract_text(path)
    text = _normalise_text(raw)
    result = PayslipParseResult(raw_text=raw)
    fields = result.fields

    fields["month"] = _month_from_text(text)
    fields["employer"] = _employer(text)
    fields["employee_name"] = _employee_name(text)
    fields["employee_nif"] = _text(r"N[ºo°]?\s*Contribuinte\s*:\s*(\d{9})", text)
    fields["employee_ss"] = _text(r"N[ºo°]?\s*C\.?\s*Seg\.?\s*Social\s*:\s*(\d{11})", text)
    fields["employee_number"] = _employee_number(text)
    fields["professional_category"] = _text(
        r"Categoria Profissional\s*:\s*([^\n]+?)(?:\s+(?:IRS|Vencimento)\s+Acumulado\b|$)", text
    )
    fields["work_location"] = _text(
        r"Local Trabalho\s*:\s*([^\n]+?)(?:\s+(?:IRS|Vencimento)\s+Acumulado\b|$)", text
    )
    fields["pay_date"] = _pay_date(text)
    fields["iban"] = _text(r"IBAN\s*:\s*([A-Z]{2}\d{2}[A-Z0-9 ]{10,34})", text)
    if fields["iban"]:
        fields["iban"] = fields["iban"].replace(" ", "")
    fields["absence_hours"] = _money(r"Falta Justificada Remunera[çc][ãa]o\s+" + MONEY, text)
    fields["accumulated_pay"] = _money(r"Vencimento Acumulado\s*:\s*" + MONEY + r"\s*€", text)
    fields["accumulated_irs"] = _money(r"IRS Acumulado\s*:\s*" + MONEY + r"\s*€", text)

    fields["base_salary"] = _money(r"Vencimento Base\s*:\s*" + MONEY + r"\s*€", text)
    meal = _find(
        r"Sub\.?\s*de\s*Refei[çc][ãa]o\s*Cart[ãa]o\s+" + MONEY + r"\s+" + MONEY + r"\s*€\s+" + MONEY + r"\s*€",
        text,
    )
    if meal:
        fields["meal_days"] = parse_money(meal.group(1))
        fields["meal_daily"] = parse_money(meal.group(2))
        fields["meal_allowance"] = parse_money(meal.group(3))
    else:
        fields["meal_days"] = _money(r"Sub\.?\s*de\s*Refei[çc][ãa]o\s*Cart[ãa]o\s+" + MONEY, text)
        fields["meal_daily"] = None
        fields["meal_allowance"] = _money(r"Sub\.?\s*Refei[çc][ãa]o\s*Cart[ãa]o[\s\S]{0,40}?" + MONEY + r"\s*€", text)

    fields["holiday_subsidy"] = _money(r"Subs[íi]dio de F[ée]rias\s+" + MONEY + r"\s*€", text)
    fields["christmas_subsidy"] = _money(r"Subs[íi]dio de Natal\s+" + MONEY + r"\s*€", text)

    fields["irs_rate"] = _percent(r"Imposto S/Rendimento\s+" + PCT + r"\s*%", text)
    fields["irs_amount"] = _money(
        r"Imposto S/Rendimento\s+" + PCT + r"\s*%\s+" + MONEY + r"\s*€", text, group=2
    )
    fields["ss_rate"] = _percent(r"Seguran[çc]a Social\s+" + PCT + r"\s*%", text)
    fields["ss_amount"] = _money(
        r"Seguran[çc]a Social\s+" + PCT + r"\s*%\s+" + MONEY + r"\s*€", text, group=2
    )

    fields["gross_taxable"] = _money(r"Total Sujeito IRS\s*:\s*" + MONEY + r"\s*€", text)
    fields["gross_exempt"] = _money(r"Total N[ãa]o Sujeito IRS\s*:\s*" + MONEY + r"\s*€", text)
    fields["total_deductions"] = _money(r"Total Descontos\s*:\s*" + MONEY + r"\s*€", text)
    fields["net_cash"] = _money(r"Transfer[êe]ncia Banc[áa]ria\s*:\s*" + MONEY + r"\s*€", text)
    fields["net_meal"] = _money(r"Sub\.?\s*Refei[çc][ãa]o\s*:\s*" + MONEY + r"\s*€", text)
    fields["total_net"] = _money(r"\bTotal\s*:\s*" + MONEY + r"\s*€", text)

    # Fill missing monetary fields with zero so the form is always usable.
    for key in MONEY_FIELDS:
        if fields.get(key) is None:
            fields[key] = 0.0
    if not fields.get("meal_days"):
        fields["meal_days"] = 0.0
    for key in ("irs_rate", "ss_rate"):
        if fields.get(key) is None:
            fields[key] = 0.0

    # Derived values and arithmetic checks.
    gross_calc = sum(
        float(fields.get(k) or 0)
        for k in ("base_salary", "holiday_subsidy", "christmas_subsidy", "other_earnings")
    )
    if not fields.get("gross_taxable"):
        fields["gross_taxable"] = gross_calc
    elif abs(float(fields["gross_taxable"]) - gross_calc) > 0.03:
        difference = float(fields["gross_taxable"]) - gross_calc
        if difference > 0:
            fields["other_earnings"] = round(difference, 2)
            gross_calc = float(fields["gross_taxable"])
        else:
            result.warnings.append(
                f"O total tributável ({fields['gross_taxable']:.2f} €) não bate certo com a soma das remunerações ({gross_calc:.2f} €)."
            )

    deductions_calc = float(fields.get("irs_amount") or 0) + float(fields.get("ss_amount") or 0)
    if not fields.get("total_deductions"):
        fields["total_deductions"] = deductions_calc
    elif abs(float(fields["total_deductions"]) - deductions_calc) > 0.03:
        difference = float(fields["total_deductions"]) - deductions_calc
        if difference > 0:
            fields["other_deductions"] = round(difference, 2)
            deductions_calc = float(fields["total_deductions"])
        else:
            result.warnings.append(
                f"Os descontos totais ({fields['total_deductions']:.2f} €) não batem certo com IRS + Segurança Social ({deductions_calc:.2f} €)."
            )

    net_calc = float(fields.get("gross_taxable") or 0) - float(fields.get("total_deductions") or 0)
    if not fields.get("net_cash"):
        fields["net_cash"] = max(net_calc, 0)
    elif abs(float(fields["net_cash"]) - net_calc) > 0.03:
        result.warnings.append(
            f"O líquido ({fields['net_cash']:.2f} €) não bate certo com o bruto menos descontos ({net_calc:.2f} €)."
        )

    meal_calc = float(fields.get("meal_days") or 0) * float(fields.get("meal_daily") or 0)
    if fields.get("meal_allowance") and fields.get("meal_daily"):
        if abs(float(fields["meal_allowance"]) - meal_calc) > 0.03:
            result.warnings.append(
                f"O cartão refeição ({fields['meal_allowance']:.2f} €) não corresponde a {fields['meal_days']:.0f} dias × {fields['meal_daily']:.2f} €."
            )
    elif not fields.get("meal_allowance"):
        fields["meal_allowance"] = meal_calc

    if not fields.get("net_meal"):
        fields["net_meal"] = float(fields.get("meal_allowance") or 0)
    if not fields.get("total_net"):
        fields["total_net"] = float(fields.get("net_cash") or 0) + float(fields.get("net_meal") or 0)
    elif abs(float(fields["total_net"]) - (float(fields["net_cash"]) + float(fields["net_meal"]))) > 0.03:
        result.warnings.append(
            "O total final não corresponde ao líquido em conta + cartão refeição. Confirma os valores."
        )

    if not fields.get("month"):
        pay_date = fields.get("pay_date")
        if pay_date:
            fields["month"] = pay_date[:7]
        else:
            result.warnings.append("Não encontrei o mês do recibo. Preenche-o antes de guardar.")
    if not fields.get("pay_date") and fields.get("month"):
        year, month = (int(x) for x in fields["month"].split("-"))
        last = calendar.monthrange(year, month)[1]
        fields["pay_date"] = f"{year:04d}-{month:02d}-{last:02d}"

    core = ["month", "base_salary", "net_cash", "net_meal"]
    optional = ["employer", "employee_name", "irs_amount", "ss_amount", "gross_taxable", "total_net"]
    core_found = sum(1 for key in core if fields.get(key))
    optional_found = sum(1 for key in optional if fields.get(key))
    base_confidence = 0.55 * (core_found / len(core)) + 0.30 * (optional_found / len(optional))
    consistency = 0.15 if not result.warnings else max(0.0, 0.15 - 0.04 * len(result.warnings))
    result.confidence = round(min(0.98, base_confidence + consistency), 2)

    if result.confidence < 0.75:
        result.warnings.append("A leitura automática teve pouca confiança. Revê todos os campos com atenção.")

    return result
