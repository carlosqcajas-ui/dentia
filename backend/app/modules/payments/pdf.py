"""Payment receipt PDF.

A receipt is the document the clinic hands the patient when money
changes hands. It is deliberately **not** an invoice: no line items, no
VAT breakdown, no fiscal numbering. The footer says so explicitly so a
patient never mistakes it for a comprobante fiscal.

Refunds are shown when present. A receipt for a fully refunded payment
must not read like a valid proof of payment — the net line and a banner
make the reversal obvious.
"""

from __future__ import annotations

from decimal import Decimal
from html import escape
from typing import TYPE_CHECKING

from app.core.pdf import format_address, html_to_pdf, money_locale
from app.core.utils.currency import format_currency as _fmt_currency

if TYPE_CHECKING:
    from app.core.auth.models import Clinic
    from app.modules.patients.models import Patient

    from .models import Payment

_LABELS: dict[str, dict[str, str]] = {
    "es": {
        "receipt": "Recibo de pago",
        "number": "N.º",
        "date": "Fecha",
        "patient": "Paciente",
        "amount_received": "Importe recibido",
        "method": "Forma de pago",
        "reference": "Referencia",
        "notes": "Observaciones",
        "applied_to": "Aplicado a",
        "budget": "Presupuesto",
        "on_account": "A cuenta del paciente",
        "refunds": "Devoluciones",
        "refunded_on": "Devuelto el",
        "net": "Importe neto",
        "fully_refunded": "PAGO DEVUELTO EN SU TOTALIDAD",
        "partially_refunded": "Este pago tiene devoluciones registradas",
        "recorded_by": "Registrado por",
        "disclaimer": (
            "Este documento acredita la recepción del importe indicado. "
            "No es una factura ni un comprobante fiscal."
        ),
        "methods": {
            "cash": "Efectivo",
            "card": "Tarjeta",
            "bank_transfer": "Transferencia",
            "direct_debit": "Domiciliación",
            "insurance": "Seguro",
            "other": "Otro",
        },
    },
    "en": {
        "receipt": "Payment receipt",
        "number": "No.",
        "date": "Date",
        "patient": "Patient",
        "amount_received": "Amount received",
        "method": "Payment method",
        "reference": "Reference",
        "notes": "Notes",
        "applied_to": "Applied to",
        "budget": "Budget",
        "on_account": "Patient account",
        "refunds": "Refunds",
        "refunded_on": "Refunded on",
        "net": "Net amount",
        "fully_refunded": "PAYMENT FULLY REFUNDED",
        "partially_refunded": "This payment has recorded refunds",
        "recorded_by": "Recorded by",
        "disclaimer": (
            "This document acknowledges receipt of the amount shown. "
            "It is not an invoice nor a fiscal document."
        ),
        "methods": {
            "cash": "Cash",
            "card": "Card",
            "bank_transfer": "Bank transfer",
            "direct_debit": "Direct debit",
            "insurance": "Insurance",
            "other": "Other",
        },
    },
    "fr": {
        "receipt": "Reçu de paiement",
        "number": "N°",
        "date": "Date",
        "patient": "Patient",
        "amount_received": "Montant reçu",
        "method": "Moyen de paiement",
        "reference": "Référence",
        "notes": "Observations",
        "applied_to": "Affecté à",
        "budget": "Devis",
        "on_account": "Compte du patient",
        "refunds": "Remboursements",
        "refunded_on": "Remboursé le",
        "net": "Montant net",
        "fully_refunded": "PAIEMENT INTÉGRALEMENT REMBOURSÉ",
        "partially_refunded": "Ce paiement comporte des remboursements",
        "recorded_by": "Enregistré par",
        "disclaimer": (
            "Ce document atteste la réception du montant indiqué. "
            "Ce n'est ni une facture ni un document fiscal."
        ),
        "methods": {
            "cash": "Espèces",
            "card": "Carte",
            "bank_transfer": "Virement",
            "direct_debit": "Prélèvement",
            "insurance": "Assurance",
            "other": "Autre",
        },
    },
}

_CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body { font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
       font-size: 10pt; color: #1f2937; margin: 0; }
.header { display: flex; justify-content: space-between;
          align-items: flex-start; border-bottom: 2px solid #111827;
          padding-bottom: 10px; margin-bottom: 22px; }
.clinic-name { font-size: 15pt; font-weight: 700; }
.clinic-meta { font-size: 8.5pt; color: #6b7280; line-height: 1.5; }
.doc-title { text-align: right; }
.doc-title h1 { font-size: 13pt; margin: 0 0 4px; text-transform: uppercase;
                letter-spacing: 0.06em; }
.doc-title .num { font-size: 11pt; font-weight: 700; }
.doc-title .date { font-size: 9pt; color: #6b7280; }
.banner { padding: 9px 12px; border-radius: 4px; margin-bottom: 18px;
          font-weight: 700; font-size: 9.5pt; }
.banner.full { background: #fee2e2; color: #991b1b; text-align: center;
               letter-spacing: 0.05em; }
.banner.partial { background: #fef3c7; color: #92400e; }
.row { display: flex; margin-bottom: 7px; }
.row .k { width: 38%; color: #6b7280; }
.row .v { width: 62%; font-weight: 600; }
.amount-box { background: #f3f4f6; border-radius: 6px; padding: 16px 18px;
              margin: 20px 0; display: flex; justify-content: space-between;
              align-items: baseline; }
.amount-box .k { font-size: 10pt; color: #4b5563; }
.amount-box .v { font-size: 20pt; font-weight: 700; }
h2 { font-size: 10pt; text-transform: uppercase; letter-spacing: 0.05em;
     color: #6b7280; margin: 22px 0 8px; }
table { width: 100%; border-collapse: collapse; }
th, td { text-align: left; padding: 6px 4px; border-bottom: 1px solid #e5e7eb;
         font-size: 9.5pt; }
th { color: #6b7280; font-weight: 600; }
td.num, th.num { text-align: right; }
.net { margin-top: 10px; display: flex; justify-content: flex-end; gap: 14px;
       font-size: 11pt; font-weight: 700; }
.sign { margin-top: 46px; display: flex; justify-content: space-between; }
.sign .box { width: 44%; }
.sign .line { border-bottom: 1px solid #9ca3af; height: 34px; }
.sign .lbl { font-size: 8.5pt; color: #6b7280; padding-top: 5px; }
.footer { margin-top: 34px; padding-top: 9px; border-top: 1px solid #e5e7eb;
          font-size: 8pt; color: #9ca3af; line-height: 1.5; }
"""


def _fmt_receipt_number(payment: "Payment") -> str:
    """Human-facing receipt reference.

    Falls back to a short slice of the UUID for rows recorded before the
    numbering migration — those exist only in databases restored from an
    old dump, but the PDF must still render.
    """
    if payment.receipt_number is None:
        return str(payment.id)[:8].upper()
    return f"REC-{payment.receipt_number:06d}"


def _allocation_rows(payment: "Payment", labels: dict, money) -> str:
    rows = []
    for alloc in payment.allocations or []:
        if alloc.target_type == "budget":
            budget = alloc.budget
            ref = getattr(budget, "budget_number", None) if budget else None
            target = f"{labels['budget']} {ref}" if ref else labels["budget"]
        else:
            target = labels["on_account"]
        rows.append(
            f"<tr><td>{escape(target)}</td>"
            f"<td class='num'>{escape(money(alloc.amount))}</td></tr>"
        )
    return "".join(rows)


def _refund_rows(payment: "Payment", labels: dict, money) -> str:
    rows = []
    for refund in payment.refunds or []:
        when = refund.refunded_at.strftime("%d/%m/%Y") if refund.refunded_at else "—"
        reason = escape(refund.reason_note or refund.reason_code or "")
        rows.append(
            f"<tr><td>{escape(when)}</td><td>{reason}</td>"
            f"<td class='num'>-{escape(money(refund.amount))}</td></tr>"
        )
    return "".join(rows)


class PaymentReceiptPDFService:
    """Renders the printable receipt for a single ``Payment``."""

    @staticmethod
    async def generate_pdf(
        payment: "Payment",
        clinic: "Clinic | None",
        patient: "Patient | None",
        locale: str = "es",
    ) -> bytes:
        """Render the receipt.

        ``payment`` must arrive with ``allocations`` (and their
        ``budget``) plus ``refunds`` eager-loaded — this runs in a
        thread and must not trigger lazy IO.
        """
        html = PaymentReceiptPDFService._build_html(payment, clinic, patient, locale)
        return await html_to_pdf(html)

    @staticmethod
    def _build_html(
        payment: "Payment",
        clinic: "Clinic | None",
        patient: "Patient | None",
        locale: str,
    ) -> str:
        labels = _LABELS.get(locale, _LABELS["es"])
        loc = money_locale(locale)

        def money(amount: Decimal) -> str:
            return _fmt_currency(amount, payment.currency, locale=loc)

        refunded = sum((r.amount for r in payment.refunds or []), Decimal("0.00"))
        net = payment.amount - refunded

        banner = ""
        if refunded >= payment.amount and payment.amount > 0:
            banner = f"<div class='banner full'>{escape(labels['fully_refunded'])}</div>"
        elif refunded > 0:
            banner = f"<div class='banner partial'>{escape(labels['partially_refunded'])}</div>"

        clinic_name = escape(clinic.name if clinic else "")
        clinic_addr = escape(format_address(getattr(clinic, "address", None)) if clinic else "")
        clinic_contact = " | ".join(
            filter(
                None,
                [
                    escape(getattr(clinic, "phone", None) or ""),
                    escape(getattr(clinic, "email", None) or ""),
                ],
            )
        )

        patient_name = ""
        if patient:
            patient_name = escape(
                " ".join(filter(None, [patient.first_name, patient.last_name])).strip()
            )

        method_label = labels["methods"].get(payment.method, payment.method)

        optional_rows = ""
        if payment.reference:
            optional_rows += (
                f"<div class='row'><div class='k'>{escape(labels['reference'])}</div>"
                f"<div class='v'>{escape(payment.reference)}</div></div>"
            )
        if payment.notes:
            optional_rows += (
                f"<div class='row'><div class='k'>{escape(labels['notes'])}</div>"
                f"<div class='v'>{escape(payment.notes)}</div></div>"
            )

        alloc_rows = _allocation_rows(payment, labels, money)
        alloc_section = (
            f"<h2>{escape(labels['applied_to'])}</h2><table>{alloc_rows}</table>"
            if alloc_rows
            else ""
        )

        refund_rows = _refund_rows(payment, labels, money)
        refund_section = ""
        if refund_rows:
            refund_section = (
                f"<h2>{escape(labels['refunds'])}</h2>"
                f"<table><thead><tr>"
                f"<th>{escape(labels['refunded_on'])}</th><th></th>"
                f"<th class='num'></th></tr></thead>"
                f"<tbody>{refund_rows}</tbody></table>"
                f"<div class='net'><span>{escape(labels['net'])}</span>"
                f"<span>{escape(money(net))}</span></div>"
            )

        return f"""<!DOCTYPE html>
<html lang="{escape(locale)}">
<head><meta charset="utf-8"><style>{_CSS}</style></head>
<body>
  <div class="header">
    <div>
      <div class="clinic-name">{clinic_name}</div>
      <div class="clinic-meta">{clinic_addr}<br>{clinic_contact}</div>
    </div>
    <div class="doc-title">
      <h1>{escape(labels["receipt"])}</h1>
      <div class="num">{escape(labels["number"])} {escape(_fmt_receipt_number(payment))}</div>
      <div class="date">{escape(labels["date"])}:
        {payment.payment_date.strftime("%d/%m/%Y") if payment.payment_date else "—"}</div>
    </div>
  </div>

  {banner}

  <div class="row"><div class="k">{escape(labels["patient"])}</div>
       <div class="v">{patient_name}</div></div>
  <div class="row"><div class="k">{escape(labels["method"])}</div>
       <div class="v">{escape(method_label)}</div></div>
  {optional_rows}

  <div class="amount-box">
    <div class="k">{escape(labels["amount_received"])}</div>
    <div class="v">{escape(money(payment.amount))}</div>
  </div>

  {alloc_section}
  {refund_section}

  <div class="sign">
    <div class="box"><div class="line"></div>
      <div class="lbl">{clinic_name}</div></div>
    <div class="box"><div class="line"></div>
      <div class="lbl">{patient_name}</div></div>
  </div>

  <div class="footer">
    {escape(labels["disclaimer"])}<br>
    {escape(labels["recorded_by"])}:
    {escape(getattr(payment.recorder, "full_name", None) or "")}
  </div>
</body>
</html>"""
