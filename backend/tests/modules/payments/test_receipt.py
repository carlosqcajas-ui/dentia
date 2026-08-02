"""Receipt numbering + receipt PDF endpoint."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.models import Clinic, ClinicMembership
from app.modules.patients.models import Patient
from app.modules.payments.models import Payment, Refund
from app.modules.payments.pdf import PaymentReceiptPDFService, _fmt_receipt_number


async def _setup_clinic(db: AsyncSession, auth_headers: dict, client: AsyncClient) -> dict:
    me = await client.get("/api/v1/auth/me", headers=auth_headers)
    user_id = me.json()["data"]["user"]["id"]

    clinic = Clinic(
        id=uuid4(),
        name="Clínica Recibo",
        tax_id="A28000000",
        timezone="America/La_Paz",
        currency="BOB",
        settings={},
    )
    db.add(clinic)
    await db.flush()
    db.add(ClinicMembership(id=uuid4(), clinic_id=clinic.id, user_id=user_id, role="admin"))

    patient = Patient(id=uuid4(), clinic_id=clinic.id, first_name="Ana", last_name="García")
    db.add(patient)
    await db.commit()

    return {
        "clinic_id": str(clinic.id),
        "user_id": user_id,
        "patient_id": str(patient.id),
    }


def _body(amount: str) -> dict:
    return {
        "patient_id": None,  # filled by caller
        "amount": amount,
        "method": "cash",
        "payment_date": date.today().isoformat(),
        "allocations": [{"target_type": "on_account", "amount": amount}],
    }


async def _create_payment(client: AsyncClient, auth_headers: dict, setup: dict, amount: str):
    body = _body(amount)
    body["patient_id"] = setup["patient_id"]
    resp = await client.post(
        f"/api/v1/payments?clinic_id={setup['clinic_id']}",
        headers=auth_headers,
        json=body,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


@pytest.mark.asyncio
async def test_receipt_numbers_are_sequential_per_clinic(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    setup = await _setup_clinic(db_session, auth_headers, client)

    first = await _create_payment(client, auth_headers, setup, "100.00")
    second = await _create_payment(client, auth_headers, setup, "50.00")
    third = await _create_payment(client, auth_headers, setup, "25.00")

    assert first["receipt_number"] == 1
    assert second["receipt_number"] == 2
    assert third["receipt_number"] == 3


@pytest.mark.asyncio
async def test_receipt_numbering_restarts_per_clinic(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """Two clinics must not share a sequence — each starts at 1."""
    clinic_a = await _setup_clinic(db_session, auth_headers, client)
    clinic_b = await _setup_clinic(db_session, auth_headers, client)

    a1 = await _create_payment(client, auth_headers, clinic_a, "10.00")
    b1 = await _create_payment(client, auth_headers, clinic_b, "10.00")
    a2 = await _create_payment(client, auth_headers, clinic_a, "10.00")

    assert a1["receipt_number"] == 1
    assert b1["receipt_number"] == 1
    assert a2["receipt_number"] == 2


@pytest.mark.asyncio
async def test_receipt_pdf_endpoint_returns_pdf(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    setup = await _setup_clinic(db_session, auth_headers, client)
    payment = await _create_payment(client, auth_headers, setup, "150.00")

    resp = await client.get(
        f"/api/v1/payments/{payment['id']}/receipt.pdf?clinic_id={setup['clinic_id']}",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert "recibo_000001.pdf" in resp.headers["content-disposition"]
    # WeasyPrint may be absent on a dev box; the helper then returns the
    # HTML. Either way the document must carry the receipt reference.
    assert resp.content[:4] == b"%PDF" or b"REC-000001" in resp.content


@pytest.mark.asyncio
async def test_receipt_pdf_404_for_other_clinic(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """Multi-tenancy: a payment is invisible from another clinic."""
    clinic_a = await _setup_clinic(db_session, auth_headers, client)
    clinic_b = await _setup_clinic(db_session, auth_headers, client)
    payment = await _create_payment(client, auth_headers, clinic_a, "80.00")

    resp = await client.get(
        f"/api/v1/payments/{payment['id']}/receipt.pdf?clinic_id={clinic_b['clinic_id']}",
        headers=auth_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_receipt_shows_refund_and_net(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """A refunded payment must not render as clean proof of payment."""
    setup = await _setup_clinic(db_session, auth_headers, client)
    created = await _create_payment(client, auth_headers, setup, "200.00")

    payment = await db_session.get(Payment, created["id"])
    db_session.add(
        Refund(
            id=uuid4(),
            clinic_id=payment.clinic_id,
            payment_id=payment.id,
            amount=Decimal("200.00"),
            method="cash",
            reason_code="duplicate",
            refunded_at=datetime.now(UTC),
            refunded_by=setup["user_id"],
        )
    )
    await db_session.commit()

    from app.modules.payments.service import PaymentService

    loaded = await PaymentService.get_for_receipt(db_session, payment.clinic_id, payment.id)
    clinic = await db_session.get(Clinic, payment.clinic_id)
    html = PaymentReceiptPDFService._build_html(loaded, clinic, loaded.patient, "es")

    assert "PAGO DEVUELTO EN SU TOTALIDAD" in html
    assert "Importe neto" in html


def test_receipt_reference_falls_back_without_number():
    """Rows predating the numbering migration still render."""

    class _Stub:
        receipt_number = None
        id = uuid4()

    assert _fmt_receipt_number(_Stub()) == str(_Stub.id)[:8].upper()


def test_receipt_reference_is_zero_padded():
    class _Stub:
        receipt_number = 42
        id = uuid4()

    assert _fmt_receipt_number(_Stub()) == "REC-000042"
