"""Synthetic demo data (spec section 10). ALL PEOPLE, FACILITIES AND VALUES ARE FICTIONAL.

Ananya Sharma's seeded history deliberately ends on 31-Oct-2025: the upload samples (Task 5) continue
her story (Nov-2025 hypoglycaemia admission -> Glimepiride stopped / Sitagliptin started; Sep-2026
prescription). Ravi Kumar's history runs to Sep-2026 and he holds an active 7-day grant to Dr. Meera Iyer.

Every clinical row is linked to a seeded text Document (status ``committed``, provider ``seed``) whose
``extracted_text`` reads like the original paper and carries ``[Page n]`` markers, so citations work.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import atomic, log_event
from app.auth import hash_password
from app.db import utcnow
from app.models import (
    Condition,
    ConsentGrant,
    DoctorProfile,
    Document,
    Encounter,
    LabResult,
    Medication,
    PatientProfile,
    User,
    Vital,
)
from app.schemas import (
    ExtractedCondition,
    ExtractedEncounter,
    ExtractedLab,
    ExtractedMedication,
    ExtractedVital,
    ExtractionResult,
)

DEMO_PASSWORD = "demo1234"
ANANYA_DHID = "DH-8923-4102-9981"
RAVI_DHID = "DH-5512-7730-2046"


def D(s: str) -> date:
    return date.fromisoformat(s)


def fmt(d: date | None) -> str:
    return d.strftime("%d-%b-%Y") if d else "-"


def inr(x: float) -> str:
    """Indian digit grouping: 68400 -> '68,400'."""
    whole = int(round(x))
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return s


def _age(dob: date, on: date) -> int:
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def _num(value) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Facilities / clinicians (fictional)
# ---------------------------------------------------------------------------

SANJEEVANI = {
    "name": "Sanjeevani Diabetes & Endocrine Clinic",
    "addr": "1st Floor, Mayur Plaza, Paud Road, Kothrud, Pune 411038 · Ph 020-2543 1180",
    "doctor": "Dr. Rajesh Menon", "quals": "MD (Medicine), DM (Endocrinology)", "reg": "MMC-2006-03318",
    "spec": "Endocrinology", "fee": 900.0, "prefix": "SDE",
}
LOTUS = {
    "name": "Lotus Heart Institute",
    "addr": "Survey No. 42, Baner Road, Pune 411045 · Ph 020-6710 2200 · NABH accredited",
    "doctor": "Dr. Meera Iyer", "quals": "MBBS, MD (General Medicine)", "reg": "MMC-2009-04512",
    "spec": "Cardiology / Internal Medicine", "fee": 1200.0, "prefix": "LHI",
}
BREATHEWELL = {
    "name": "BreatheWell Chest & Allergy Clinic",
    "addr": "No. 18, 100 Feet Road, Indiranagar, Bengaluru 560038 · Ph 080-4123 7788",
    "doctor": "Dr. Kavitha Rao", "quals": "MBBS, MD (Pulmonary Medicine)", "reg": "KMC-2011-58210",
    "spec": "Pulmonology", "fee": 800.0, "prefix": "BWC",
}
CITYCARE = {
    "name": "CityCare Diagnostics", "addr": "Shop 3-5, Karishma Society, Karve Road, Kothrud, Pune 411038",
    "pathologist": "Dr. S. Kulkarni, MD (Pathology)", "nabl": "NABL MC-4471",
}
NIRAMAYA_LAB = {
    "name": "Niramaya Diagnostics & Lung Function Lab", "addr": "HAL 2nd Stage, Indiranagar, Bengaluru 560008",
    "pathologist": "Dr. P. Shenoy, MD (Pathology)", "nabl": "NABL MC-5120",
}
AROGYA = {"name": "Arogya Medicals", "addr": "Karve Road, Kothrud, Pune 411038", "gstin": "27ABCPA1234F1Z5",
          "dl": "MH-PZ3-112045"}
NEEM = {"name": "Neem Tree Pharmacy", "addr": "CMH Road, Indiranagar, Bengaluru 560038", "gstin": "29AAKFN5678K1Z2",
        "dl": "KA-B32-20231187"}


# ---------------------------------------------------------------------------
# Generic document + rows writer
# ---------------------------------------------------------------------------

def _pages_text(pages: list[str]) -> str:
    return "\n\n".join(f"[Page {i}]\n{p.strip()}" for i, p in enumerate(pages, start=1))


def _iso(v):
    return v.isoformat() if isinstance(v, date) else v


def _pick(model, item: dict) -> dict:
    return {k: _iso(v) for k, v in item.items() if k in model.model_fields}


def add_document(db: Session, patient: User, *, filename: str, doc_type: str, doc_date: date, facility: str,
                 doctor: str | None, specialization: str | None, summary: str, pages: list[str],
                 conditions=(), medications=(), labs=(), vitals=(), encounter: dict | None = None,
                 lab_name: str | None = None) -> Document:
    """Create a committed seed Document plus its clinical rows (all with source refs)."""
    text = _pages_text(pages)
    extraction = ExtractionResult(
        doc_type=doc_type, document_date=doc_date.isoformat(), facility=facility, doctor_name=doctor,
        specialization=specialization, summary=summary,
        conditions=[ExtractedCondition(**_pick(ExtractedCondition, c)) for c in conditions],
        medications=[ExtractedMedication(**_pick(ExtractedMedication, m)) for m in medications],
        lab_results=[ExtractedLab(**_pick(ExtractedLab, {"date": doc_date, **lab})) for lab in labs],
        vitals=[ExtractedVital(**_pick(ExtractedVital, {"date": doc_date, **v})) for v in vitals],
        encounter=ExtractedEncounter(**_pick(ExtractedEncounter, encounter)) if encounter else None,
        confidence=1.0, warnings=[],
    )
    raw = text.encode("utf-8")
    doc = Document(patient_id=patient.id, filename=filename, mime="text/plain", kind="text", size=len(raw),
                   content=raw, status="committed", doc_type=doc_type, extracted_text=text,
                   extraction=json.loads(extraction.model_dump_json()), provider="seed",
                   uploaded_at=_uploaded_at(doc_date), document_date=doc_date)
    db.add(doc)
    db.flush()

    def src(item: dict) -> dict:
        return {"patient_id": patient.id, "source_document_id": doc.id, "source_page": item.get("page", 1)}

    for c in conditions:
        db.add(Condition(name=c["name"], diagnosed_date=c.get("diagnosed_date"), severity=c.get("severity"),
                         status=c.get("status", "active"), **src(c)))
    for m in medications:
        if m.get("row") is False:  # printed on the prescription, but already recorded from an earlier document
            continue
        db.add(Medication(drug_name=m["drug_name"], dosage=m.get("dosage"), frequency=m.get("frequency"),
                          route=m.get("route", "oral"), duration=m.get("duration"),
                          start_date=m.get("start_date", doc_date), end_date=m.get("end_date"),
                          prescriber=m.get("prescriber", doctor), active=m.get("active", True),
                          instructions=m.get("instructions"), **src(m)))
    for lab in labs:
        db.add(LabResult(test_name=lab["test_name"], value=str(lab["value"]),
                         value_num=lab.get("value_num", _num(lab["value"])), unit=lab.get("unit"),
                         ref_range=lab.get("ref_range"), flag=lab.get("flag"), date=lab.get("date", doc_date),
                         lab_name=lab_name or facility, **src(lab)))
    for v in vitals:
        value_num = v.get("value_num", None if v["kind"] == "bp" else _num(v["value"]))
        db.add(Vital(kind=v["kind"], value=str(v["value"]), value_num=value_num, unit=v.get("unit"),
                     date=v.get("date", doc_date), **src(v)))
    if encounter:
        e = dict(encounter)
        db.add(Encounter(type=e["type"], date=e.get("date", doc_date), end_date=e.get("end_date"),
                         facility=e.get("facility", facility), doctor_name=e.get("doctor_name", doctor),
                         specialization=e.get("specialization", specialization), reason=e.get("reason"),
                         diagnosis=e.get("diagnosis"), notes=e.get("notes"), advice=e.get("advice"),
                         follow_up_date=e.get("follow_up_date"), fee=e.get("fee"), **src(e)))
    return doc


def _uploaded_at(doc_date: date) -> datetime:
    """Pretend the patient uploaded each paper a day after it was issued (10:30 IST ~= 05:00 UTC)."""
    return datetime(doc_date.year, doc_date.month, doc_date.day, 5, 0) + timedelta(days=1)


# ---------------------------------------------------------------------------
# Document renderers
# ---------------------------------------------------------------------------

def _rx_line(i: int, m: dict) -> str:
    head = " ".join(x for x in (m.get("form", "Tab"), m["drug_name"], m.get("dosage")) if x)
    parts = [f"{i}. {head}"]
    sched = " ".join(x for x in (m.get("pattern"), f"({m['frequency']})" if m.get("frequency") else None) if x)
    if sched:
        parts.append(sched)
    if m.get("instructions"):
        parts.append(m["instructions"])
    if m.get("duration"):
        parts.append(f"x {m['duration']}")
    return " — ".join(parts)


def _vitals_line(vitals: list[dict], height_cm: float | None = None) -> str:
    labels = {"bp": "BP", "pulse": "Pulse", "weight": "Weight", "spo2": "SpO2", "temp": "Temp", "glucose": "CBG"}
    bits = [f"{labels[v['kind']]} {v['value']} {v.get('unit', '')}".strip() for v in vitals]
    w = next((v for v in vitals if v["kind"] == "weight"), None)
    if w and height_cm:
        bmi = float(w["value"]) / (height_cm / 100) ** 2
        bits.append(f"BMI {bmi:.1f} kg/m2")
    return " | ".join(bits)


def opd_note(clinic: dict, patient: User, visit_date: date, opd_no: str, *, complaints: str, history: str,
             examination: str, vitals: list[dict], investigations: str, diagnosis: list[str], rx: list[dict],
             advice: str, follow_up: str, fee: float, height_cm: float | None, extra_page: str | None = None) -> list[str]:
    p = patient.patient_profile
    sex = {"F": "F", "M": "M"}.get(p.sex or "", "-")
    page1 = f"""
{clinic['name'].upper()}
{clinic['addr']}
{clinic['doctor']}, {clinic['quals']} · Reg. No. {clinic['reg']}
------------------------------------------------------------------
OPD CONSULTATION NOTE                                Date: {fmt(visit_date)}
Patient: {patient.full_name}    Age/Sex: {_age(p.dob, visit_date)} Y / {sex}    DHID: {p.dhid}
OPD No: {opd_no}

Chief complaints: {complaints}
History: {history}
Vitals: {_vitals_line(vitals, height_cm)}
Examination: {examination}
Investigations reviewed: {investigations}

Diagnosis / Impression:
""" + "\n".join(f"  - {d}" for d in diagnosis) + """

Rx
""" + "\n".join(_rx_line(i, m) for i, m in enumerate(rx, start=1)) + f"""

Advice: {advice}
Follow-up: {follow_up}

Consultation fee: Rs. {inr(fee)}/- (paid)
                                                    {clinic['doctor']}
                                                    {clinic['quals']}
"""
    pages = [page1]
    if extra_page:
        pages.append(extra_page)
    return pages


def lab_report(lab: dict, patient: User, sample_date: date, report_no: str, referred_by: str,
               sections: list[tuple[str, list[dict]]], fee: float) -> list[str]:
    """One page per section. Each test dict: test_name, label, value, unit, ref_range, flag, method?"""
    p = patient.patient_profile
    pages = []
    for idx, (title, tests) in enumerate(sections, start=1):
        rows = []
        for t in tests:
            flag = {"high": "H", "low": "L"}.get(t.get("flag") or "", "")
            rows.append(f"{t.get('label', t['test_name']):<38} {str(t['value']):>8} {flag:<2} "
                        f"{t.get('unit', ''):<12} {t.get('ref_range', '')}")
        footer = (f"\nBill amount: Rs. {inr(fee)}/- (paid)   *** End of report ***\n{lab['pathologist']}"
                  if idx == len(sections) else "\n(continued on next page)")
        pages.append(f"""
{lab['name'].upper()} · {lab['nabl']}
{lab['addr']}
LABORATORY REPORT                                   Report No: {report_no}
Patient: {patient.full_name}   Age/Sex: {_age(p.dob, sample_date)} Y / {p.sex}   DHID: {p.dhid}
Referred by: {referred_by}
Sample collected: {fmt(sample_date)} 08:10   Reported: {fmt(sample_date)} 17:45   Page {idx} of {len(sections)}

{title.upper()}
{'TEST':<38} {'RESULT':>8} {'':<2} {'UNIT':<12} BIOLOGICAL REF. RANGE
{chr(10).join(rows)}
{footer}
""")
    return pages


def pharmacy_bill(shop: dict, patient: User, bill_date: date, bill_no: str, prescriber: str,
                  items: list[tuple[str, str, int, float]]) -> tuple[list[str], float]:
    """items: (product, batch/expiry, qty, amount)."""
    lines = [f"{i}. {name:<46} {batch:<16} {qty:>3} {inr(amt):>8}" for i, (name, batch, qty, amt) in
             enumerate(items, start=1)]
    gross = sum(a for *_, a in items)
    disc = round(gross * 0.10)
    net = gross - disc
    text = f"""
{shop['name'].upper()} — Chemists & Druggists
{shop['addr']} · GSTIN {shop['gstin']} · DL No. {shop['dl']}
TAX INVOICE / CASH MEMO                         Bill No: {bill_no}   Date: {fmt(bill_date)}
Patient: {patient.full_name}        Prescriber: {prescriber}

#  {'PRODUCT':<46} {'BATCH/EXP':<16} QTY   AMOUNT
{chr(10).join(lines)}

Gross: Rs. {inr(gross)}   Less 10% discount: Rs. {inr(disc)}
NET PAYABLE: Rs. {inr(net)}/- (UPI)     Prices inclusive of GST
Thank you. Get well soon.
"""
    return [text], float(net)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def _create_users(db: Session) -> dict[str, User]:
    pw = hash_password(DEMO_PASSWORD)
    ananya = User(email="ananya@demo.in", password_hash=pw, full_name="Ananya Sharma", role="patient")
    ravi = User(email="ravi@demo.in", password_hash=pw, full_name="Ravi Kumar", role="patient")
    meera = User(email="meera@demo.in", password_hash=pw, full_name="Dr. Meera Iyer", role="doctor")
    admin = User(email="admin@demo.in", password_hash=pw, full_name="Vault Administrator", role="admin")
    db.add_all([ananya, ravi, meera, admin])
    db.flush()
    db.add(PatientProfile(
        user_id=ananya.id, dhid=ANANYA_DHID, dob=D("1968-04-12"), sex="F", phone="+91 98220 41176",
        address="Flat 402, Shubham Residency, Paud Road, Kothrud, Pune 411038", occupation="School teacher",
        blood_group="B+", height_cm=158.0, weight_kg=70.5,
        allergies=["Penicillin (urticaria, 2010)"],
        hereditary_history=[{"relation": "Father", "condition": "Type 2 diabetes mellitus"},
                            {"relation": "Mother", "condition": "Coronary artery disease (MI at 62)"}],
        insurance={"provider": "Suraksha Health Insurance (synthetic)", "policy_number": "SHI-PN-2291-7745",
                   "coverage": "Rs. 5,00,000 family floater"},
        family_members=[{"name": "Vikram Sharma", "relation": "Husband"},
                        {"name": "Riya Sharma", "relation": "Daughter"}],
        emergency_contact="Vikram Sharma (husband) +91 98220 41177",
    ))
    db.add(PatientProfile(
        user_id=ravi.id, dhid=RAVI_DHID, dob=D("1990-07-22"), sex="M", phone="+91 99001 22345",
        address="No. 7, 2nd Cross, HAL 3rd Stage, Indiranagar, Bengaluru 560075", occupation="Software engineer",
        blood_group="O+", height_cm=176.0, weight_kg=72.0,
        allergies=["Aspirin / NSAIDs (bronchospasm)", "House dust mite"],
        hereditary_history=[{"relation": "Mother", "condition": "Bronchial asthma"}],
        insurance={"provider": "Arogya Raksha Group Mediclaim (synthetic)", "policy_number": "ARG-BLR-55120-08",
                   "coverage": "Rs. 3,00,000 (employer group cover)"},
        family_members=[{"name": "Lakshmi Kumar", "relation": "Mother"}],
        emergency_contact="Lakshmi Kumar (mother) +91 99001 22346",
    ))
    db.add(DoctorProfile(user_id=meera.id, license_no=LOTUS["reg"], specialization="Cardiologist / Consultant Physician",
                         qualification=LOTUS["quals"], facility=LOTUS["name"] + ", Pune"))
    db.flush()
    for u in (ananya, ravi, meera, admin):
        db.refresh(u)
    return {"ananya": ananya, "ravi": ravi, "meera": meera, "admin": admin}


# ---------------------------------------------------------------------------
# Ananya Sharma — T2DM (2019), HTN (2021), dyslipidaemia, stable angina (2024)
# ---------------------------------------------------------------------------

def _rx(drug, dosage, frequency, pattern, instructions=None, duration=None, form="Tab", route="oral", **extra):
    return {"drug_name": drug, "dosage": dosage, "frequency": frequency, "pattern": pattern,
            "instructions": instructions, "duration": duration, "form": form, "route": route, **extra}


METFORMIN_500 = dict(drug="Glycomet (Metformin)", dosage="500 mg", frequency="BD", pattern="1-0-1",
                     instructions="after meals")
METFORMIN_SR_1000 = dict(drug="Glycomet-SR (Metformin SR)", dosage="1000 mg", frequency="BD", pattern="1-0-1",
                         instructions="after meals")
GLIM_1 = dict(drug="Amaryl (Glimepiride)", dosage="1 mg", frequency="OD", pattern="1-0-0",
              instructions="15 min before breakfast")
GLIM_2 = dict(drug="Amaryl (Glimepiride)", dosage="2 mg", frequency="OD", pattern="1-0-0",
              instructions="15 min before breakfast; do not skip meals")
TELMA_40 = dict(drug="Telma (Telmisartan)", dosage="40 mg", frequency="OD", pattern="1-0-0",
                instructions="morning")
ATORVA_20 = dict(drug="Atorva (Atorvastatin)", dosage="20 mg", frequency="HS", pattern="0-0-1",
                 instructions="at bedtime")
ECOSPRIN_75 = dict(drug="Ecosprin (Aspirin)", dosage="75 mg", frequency="OD", pattern="0-1-0",
                   instructions="after lunch")
METOLAR_25 = dict(drug="Metolar XR (Metoprolol succinate)", dosage="25 mg", frequency="OD", pattern="1-0-0",
                  instructions="morning; check pulse")
SORBITRATE = dict(drug="Sorbitrate (Isosorbide dinitrate)", dosage="5 mg", frequency="SOS", pattern="SOS",
                  instructions="sublingual for chest pain; repeat once after 5 min if needed", route="sublingual")
PANTOCID_40 = dict(drug="Pantocid (Pantoprazole)", dosage="40 mg", frequency="OD", pattern="1-0-0",
                   instructions="before breakfast", duration="4 weeks")


def _seed_ananya(db: Session, ananya: User) -> None:
    H = ananya.patient_profile.height_cm
    menon, meera = SANJEEVANI["doctor"], LOTUS["doctor"]

    # ---- Lab reports (CityCare) -------------------------------------------------
    def lab_doc(sample: str, report_no: str, referred: str, sections, fee: float, summary: str):
        d = D(sample)
        pages = lab_report(CITYCARE, ananya, d, report_no, referred, sections, fee)
        labs = [{**t, "page": i} for i, (_, tests) in enumerate(sections, start=1) for t in tests]
        for t in labs:
            t.pop("label", None)
        add_document(db, ananya, filename=f"CityCare_Lab_Report_{d:%d%b%Y}.txt", doc_type="lab_report", doc_date=d,
                     facility=CITYCARE["name"], doctor=CITYCARE["pathologist"].split(",")[0],
                     specialization="Pathology", summary=summary, pages=pages, labs=labs,
                     encounter={"type": "diagnostic", "reason": "Laboratory investigations", "fee": fee,
                                "notes": f"Referred by {referred}", "doctor_name": None,
                                "specialization": "Pathology", "page": len(sections)})

    def dm(hba1c, fbs, ppbs):
        def flag(v, hi):
            return "high" if v > hi else "normal"
        return [
            {"test_name": "HbA1c", "label": "Glycated haemoglobin (HbA1c), HPLC", "value": str(hba1c), "unit": "%",
             "ref_range": "4.0-5.6 (ADA target <7.0)", "flag": flag(hba1c, 5.6)},
            {"test_name": "FBS", "label": "Fasting blood sugar (FBS)", "value": str(fbs), "unit": "mg/dL",
             "ref_range": "70-100", "flag": flag(fbs, 100)},
            {"test_name": "PPBS", "label": "Post-prandial blood sugar (PPBS, 2 h)", "value": str(ppbs),
             "unit": "mg/dL", "ref_range": "<140", "flag": flag(ppbs, 140)},
        ]

    def lipid(tc, ldl, hdl, tg):
        return [
            {"test_name": "Total Cholesterol", "label": "Total cholesterol", "value": str(tc), "unit": "mg/dL",
             "ref_range": "<200", "flag": "high" if tc >= 200 else "normal"},
            {"test_name": "LDL", "label": "LDL cholesterol (direct)", "value": str(ldl), "unit": "mg/dL",
             "ref_range": "<100", "flag": "high" if ldl >= 100 else "normal"},
            {"test_name": "HDL", "label": "HDL cholesterol", "value": str(hdl), "unit": "mg/dL",
             "ref_range": ">50 (female)", "flag": "low" if hdl < 50 else "normal"},
            {"test_name": "Triglycerides", "label": "Triglycerides", "value": str(tg), "unit": "mg/dL",
             "ref_range": "<150", "flag": "high" if tg >= 150 else "normal"},
        ]

    def renal(creat, acr=None, egfr=None):
        rows = [{"test_name": "Creatinine", "label": "Serum creatinine", "value": f"{creat:.1f}", "unit": "mg/dL",
                 "ref_range": "0.5-1.1", "flag": "high" if creat > 1.1 else "normal"}]
        if egfr is not None:
            rows.append({"test_name": "eGFR", "label": "eGFR (CKD-EPI 2021)", "value": str(egfr),
                         "unit": "mL/min/1.73m2", "ref_range": ">90", "flag": "low" if egfr < 90 else "normal"})
        if acr is not None:
            rows.append({"test_name": "Urine ACR", "label": "Urine albumin/creatinine ratio", "value": str(acr),
                         "unit": "mg/g", "ref_range": "<30", "flag": "high" if acr >= 30 else "normal"})
        return rows

    ref_menon = f"{menon} (Sanjeevani)"
    lab_doc("2023-10-10", "CCD/2310/08812", ref_menon,
            [("Diabetes profile", dm(8.9, 182, 264)),
             ("Lipid profile & renal function", lipid(212, 138, 42, 188) + renal(0.8, egfr=86))], 2450,
            "HbA1c 8.9% with FBS 182 and PPBS 264 mg/dL indicating poor glycaemic control; "
            "LDL 138 and triglycerides 188 mg/dL raised, HDL low; creatinine normal.")
    lab_doc("2024-01-16", "CCD/2401/01174", ref_menon, [("Diabetes profile", dm(8.6, 171, 248))], 1250,
            "HbA1c 8.6% with FBS 171 and PPBS 248 mg/dL; glycaemic control remains poor.")
    lab_doc("2024-04-15", "CCD/2404/05530", ref_menon,
            [("Diabetes profile", dm(8.3, 158, 232) + [
                {"test_name": "TSH", "label": "TSH (3rd gen.)", "value": "2.6", "unit": "uIU/mL",
                 "ref_range": "0.4-4.5", "flag": "normal"}]),
             ("Lipid profile & renal function", lipid(218, 142, 40, 196) + renal(0.8, egfr=85))], 2900,
            "HbA1c 8.3% (improving); LDL 142 mg/dL and triglycerides 196 mg/dL high; TSH and creatinine normal.")
    lab_doc("2024-10-15", "CCD/2410/09918", ref_menon,
            [("Diabetes profile", dm(7.9, 142, 205)),
             ("Lipid profile & renal function", lipid(162, 96, 44, 160) + renal(0.9, egfr=78))], 2600,
            "HbA1c 7.9%; LDL 96 mg/dL on atorvastatin (within lab range but above the <70 target for CAD); "
            "creatinine 0.9 mg/dL.")
    lab_doc("2025-01-20", "CCD/2501/00987", ref_menon,
            [("Diabetes profile & urine", dm(7.6, 136, 192) + [
                {"test_name": "Urine ACR", "label": "Urine albumin/creatinine ratio", "value": "24", "unit": "mg/g",
                 "ref_range": "<30", "flag": "normal"}])], 1850,
            "HbA1c 7.6% (steady improvement); urine ACR 24 mg/g, no microalbuminuria.")
    lab_doc("2025-04-21", "CCD/2504/04411", f"{meera} (Lotus Heart Institute)",
            [("Diabetes profile", dm(7.4, 128, 184)),
             ("Lipid profile & renal function", lipid(152, 78, 45, 148) + renal(1.0, egfr=73))], 2300,
            "HbA1c 7.4%; LDL 78 mg/dL, close to the <70 target; creatinine 1.0 mg/dL.")
    lab_doc("2025-07-19", "CCD/2507/07730", ref_menon, [("Diabetes profile", dm(7.2, 122, 176))], 1250,
            "HbA1c 7.2% with FBS 122 and PPBS 176 mg/dL; near target.")
    lab_doc("2025-10-13", "CCD/2510/10162", ref_menon,
            [("Diabetes profile", dm(7.1, 118, 168)),
             ("Lipid profile & renal function", lipid(148, 72, 46, 142) + renal(1.0, acr=28, egfr=72))], 2950,
            "HbA1c 7.1% (best in two years); LDL 72 mg/dL; creatinine 1.0 mg/dL, eGFR 72, urine ACR 28 mg/g "
            "(upper-normal).")

    # ---- OPD visits ---------------------------------------------------------------
    def opd(clinic, when: str, opd_no: str, *, vitals, rx, conditions=(), med_rows=None, enc: dict, summary: str,
            **note):
        d = D(when)
        pages = opd_note(clinic, ananya, d, opd_no, vitals=vitals, rx=rx, fee=clinic["fee"], height_cm=H,
                         advice=enc["advice"], follow_up=note.pop("follow_up_text"), **note)
        meds = []
        rows = med_rows or {}
        for m in rx:
            item = {k: v for k, v in m.items() if k not in ("pattern", "form")}
            item.update(rows.get(m["drug_name"] + m["dosage"], {"row": False}))
            meds.append(item)
        add_document(db, ananya, filename=f"{clinic['prefix']}_OPD_{d:%d%b%Y}.txt", doc_type="opd_note", doc_date=d,
                     facility=clinic["name"], doctor=clinic["doctor"], specialization=clinic["spec"],
                     summary=summary, pages=pages, conditions=conditions, medications=meds, vitals=vitals,
                     encounter={"type": "opd", "fee": clinic["fee"], **enc})

    opd(SANJEEVANI, "2023-10-14", "SDE/23/10/0457",
        complaints="Routine diabetes review. Tiredness, increased thirst and nocturia (2-3/night) x 2 months.",
        history="Known T2DM since Mar-2019 on Metformin; Glimepiride added Jun-2022. Hypertension since Aug-2021 "
                "on Telmisartan. Father T2DM, mother CAD. Non-smoker, vegetarian. Penicillin allergy (urticaria).",
        examination="Conscious, oriented. No pedal oedema. Monofilament sensation intact both feet. "
                    "Peripheral pulses palpable. CVS/RS: NAD.",
        vitals=[{"kind": "bp", "value": "148/92", "unit": "mmHg"}, {"kind": "pulse", "value": "82", "unit": "/min"},
                {"kind": "weight", "value": "74.0", "unit": "kg"}],
        investigations="CityCare 10-Oct-2023: HbA1c 8.9 %, FBS 182, PPBS 264 mg/dL, LDL 138, TG 188, HDL 42 mg/dL, "
                       "S. creatinine 0.8 mg/dL.",
        diagnosis=["Type 2 diabetes mellitus — uncontrolled (HbA1c 8.9%)", "Essential hypertension — suboptimal control",
                   "Mixed dyslipidaemia (diet trial)"],
        rx=[_rx(**METFORMIN_500, duration="3 months"), _rx(**GLIM_1, duration="3 months"),
            _rx(**TELMA_40, duration="3 months")],
        conditions=[{"name": "Type 2 diabetes mellitus", "diagnosed_date": D("2019-03-15"), "severity": "moderate",
                     "status": "active"},
                    {"name": "Essential hypertension", "diagnosed_date": D("2021-08-10"), "severity": "mild",
                     "status": "active"}],
        med_rows={
            "Glycomet (Metformin)500 mg": {"start_date": D("2019-03-15"), "end_date": D("2024-01-20"), "active": False},
            "Amaryl (Glimepiride)1 mg": {"start_date": D("2022-06-04"), "end_date": D("2024-10-19"), "active": False},
            "Telma (Telmisartan)40 mg": {"start_date": D("2021-08-10"), "active": True},
        },
        follow_up_text="3 months with HbA1c, FBS, PPBS. SOS if symptomatic.",
        enc={"reason": "Diabetes and hypertension review", "follow_up_date": D("2024-01-20"),
             "diagnosis": "T2DM uncontrolled (HbA1c 8.9%); essential hypertension, suboptimal control",
             "notes": "Poor control attributed to dietary lapses during festive season; adherence counselled.",
             "advice": "1500 kcal diabetic diet, avoid sweets and fruit juices; brisk walk 30 min/day, 5 days/week; "
                       "salt < 5 g/day; home BP log twice weekly."},
        summary="Diabetes/hypertension review: HbA1c 8.9% uncontrolled, BP 148/92. Continued Metformin 500 BD, "
                "Glimepiride 1 mg and Telmisartan 40 mg with intensive lifestyle advice.")

    opd(SANJEEVANI, "2024-01-20", "SDE/24/01/0213",
        complaints="Follow-up. Thirst better; occasional fatigue. No hypoglycaemia.",
        history="Partial adherence to diet. Walking 3 days/week.",
        examination="No pedal oedema. Feet: no ulcers, sensation intact.",
        vitals=[{"kind": "bp", "value": "142/90", "unit": "mmHg"}, {"kind": "pulse", "value": "80", "unit": "/min"},
                {"kind": "weight", "value": "74.0", "unit": "kg"}],
        investigations="CityCare 16-Jan-2024: HbA1c 8.6 %, FBS 171, PPBS 248 mg/dL.",
        diagnosis=["T2DM — uncontrolled (HbA1c 8.6%)", "Essential hypertension"],
        rx=[_rx(**METFORMIN_SR_1000, duration="3 months"), _rx(**GLIM_1, duration="3 months"),
            _rx(**TELMA_40, duration="3 months")],
        med_rows={"Glycomet-SR (Metformin SR)1000 mg": {"start_date": D("2024-01-20"), "active": True}},
        follow_up_text="3 months with HbA1c, FBS/PPBS, lipid profile, TSH.",
        enc={"reason": "Diabetes follow-up", "follow_up_date": D("2024-04-20"),
             "diagnosis": "T2DM uncontrolled (HbA1c 8.6%)",
             "notes": "Metformin stepped up from 500 mg BD to Glycomet-SR 1000 mg BD (GI tolerance counselled).",
             "advice": "Continue diet plan; increase walking to daily 30-40 min; reduce rice portions at dinner."},
        summary="HbA1c 8.6%; Metformin increased to Glycomet-SR 1000 mg BD, Glimepiride 1 mg and Telmisartan 40 mg "
                "continued.")

    opd(SANJEEVANI, "2024-04-20", "SDE/24/04/0588",
        complaints="Follow-up. New: heaviness in chest on climbing two flights of stairs x 3 weeks, settles with "
                   "rest in 3-4 minutes. No rest pain.",
        history="On Glycomet-SR 1000 BD, Amaryl 1 mg, Telma 40.",
        examination="BP mildly raised. HS normal, no murmur. Chest clear.",
        vitals=[{"kind": "bp", "value": "140/88", "unit": "mmHg"}, {"kind": "pulse", "value": "84", "unit": "/min"},
                {"kind": "weight", "value": "73.5", "unit": "kg"}],
        investigations="CityCare 15-Apr-2024: HbA1c 8.3 %, FBS 158, PPBS 232; LDL 142, TG 196, HDL 40 mg/dL; "
                       "TSH 2.6; creatinine 0.8. ECG today: sinus rhythm, no acute changes.",
        diagnosis=["T2DM — improving (HbA1c 8.3%)", "Dyslipidaemia (LDL 142 mg/dL)",
                   "Exertional chest heaviness — ? angina; cardiology evaluation advised"],
        rx=[_rx(**METFORMIN_SR_1000, duration="3 months"), _rx(**GLIM_1, duration="3 months"),
            _rx(**TELMA_40, duration="3 months")],
        conditions=[{"name": "Dyslipidaemia", "diagnosed_date": D("2024-04-20"), "severity": "moderate",
                     "status": "active"}],
        follow_up_text="Cardiology consultation (TMT/Echo) within 2 weeks — URGENT if pain at rest. Endocrine review "
                       "in 3 months.",
        enc={"reason": "Diabetes follow-up; exertional chest heaviness", "follow_up_date": D("2024-07-20"),
             "diagnosis": "T2DM improving; dyslipidaemia; ? effort angina",
             "notes": "Statin advised for LDL 142 in a diabetic; patient requested a 3-month diet trial first. "
                      "Referred to cardiology for exertional symptoms.",
             "advice": "Low-fat, high-fibre diet; avoid fried snacks; walk on level ground only until cardiology "
                       "review; go to emergency if chest pain at rest > 10 min."},
        summary="HbA1c 8.3% improving; LDL 142 high (statin deferred at patient request); new exertional chest "
                "heaviness — referred to cardiology.")

    # ---- IPD admission (Lotus Heart Institute) -----------------------------------
    adm, dis = D("2024-06-22"), D("2024-06-25")
    p = ananya.patient_profile
    discharge_rx = [_rx(**METFORMIN_SR_1000), _rx(**GLIM_1), _rx(**TELMA_40), _rx(**ATORVA_20), _rx(**ECOSPRIN_75),
                    _rx(**METOLAR_25), _rx(**SORBITRATE), _rx(**PANTOCID_40)]
    dis_pages = [f"""
LOTUS HEART INSTITUTE
{LOTUS['addr']}
DISCHARGE SUMMARY                                         IP No: LHI/IP/24/06/1187
Patient: {ananya.full_name}   Age/Sex: {_age(p.dob, adm)} Y / F   DHID: {p.dhid}   Bed: CCU-4 -> Ward 3B-12
Date of admission: {fmt(adm)} 21:40       Date of discharge: {fmt(dis)} 12:00
Consultant: {LOTUS['doctor']}, {LOTUS['quals']} (Cardiology / Internal Medicine)

FINAL DIAGNOSIS:
  1. Chronic stable angina — coronary artery disease, single-vessel (mid-LAD 50%, non-critical)
  2. Type 2 diabetes mellitus — suboptimally controlled (HbA1c 8.1%)
  3. Essential hypertension
  4. Dyslipidaemia

PRESENTING COMPLAINTS: Retrosternal chest tightness on exertion x 6 weeks (walking 200 m / climbing stairs),
one episode at rest on the evening of admission lasting ~30 min, associated with sweating; no syncope.

HISTORY: T2DM since 2019, hypertension since 2021. Cardiology review advised by Dr. Rajesh Menon in Apr-2024
was deferred by the patient. Mother had CAD. Allergy: Penicillin (urticaria).

ON ADMISSION: BP 156/94 mmHg, pulse 92/min regular, SpO2 97% on room air, weight 73 kg. CVS: S1 S2 normal,
no murmur. Chest: clear. No signs of heart failure.
""", f"""
INVESTIGATIONS
ECG ({fmt(adm)}): sinus rhythm 90/min, 1 mm horizontal ST depression V4-V6; no Q waves.
Hs-Troponin I: <0.01 ng/mL at 0 h and 6 h (ref <0.04) — negative x 2.
2D Echo: LVEF 58%, no regional wall motion abnormality, grade I diastolic dysfunction, no MR.
Coronary angiogram (24-Jun-2024, right radial): LMCA normal; LAD 50% mid-segment stenosis (non-critical);
LCx 30% plaque; RCA normal. Advised optimal medical therapy; PCI not indicated at present.

LABORATORY (23-Jun-2024)
HbA1c                       8.1   H   %        4.0-5.6
Fasting blood sugar         149   H   mg/dL    70-100
LDL cholesterol             146   H   mg/dL    <100
Serum creatinine            0.9       mg/dL    0.5-1.1
Haemoglobin                 11.8  L   g/dL     12.0-15.5
Hs-Troponin I               <0.01     ng/mL    <0.04
""", f"""
HOSPITAL COURSE: Admitted to CCU; NSTE-ACS ruled out with serial troponins. Managed with antiplatelet,
high-intensity lipid lowering discussed (started Atorvastatin 20 mg given patient preference and LFT
monitoring), beta-blocker and PRN nitrate. Angiography showed non-critical disease. Pain-free from day 2,
ambulated without symptoms. Diabetes managed with basal-bolus insulin in hospital, back to oral agents at discharge.

CONDITION AT DISCHARGE: Stable. BP 132/82 mmHg, pulse 68/min.

DISCHARGE MEDICATIONS
""" + "\n".join(_rx_line(i, m) for i, m in enumerate(discharge_rx, start=1)) + f"""

ADVICE: Cardiac diet (low salt < 5 g, low saturated fat), no heavy exertion x 2 weeks then graded walking;
Note: beta-blocker may mask warning symptoms of low sugar — check CBG if unwell; carry glucose tablets.
Return immediately for chest pain > 5 min not relieved by Sorbitrate.
FOLLOW-UP: {LOTUS['doctor']} after 4 weeks (27-Jul-2024) with lipid profile; continue endocrinology follow-up.

BILL SUMMARY: Total Rs. {inr(68400)}/- (CCU 2 days, ward 1 day, coronary angiography, investigations, pharmacy).
Cashless approved by Suraksha Health Insurance: Rs. {inr(55000)}/-; paid by patient: Rs. {inr(13400)}/-.

{LOTUS['doctor']}, {LOTUS['quals']}
"""]
    started = {"start_date": dis, "active": True, "page": 3}
    add_document(
        db, ananya, filename="Lotus_Heart_Discharge_Summary_25Jun2024.txt", doc_type="discharge_summary",
        doc_date=dis, facility=LOTUS["name"], doctor=meera, specialization=LOTUS["spec"],
        summary="Admitted 22-25 Jun 2024 with exertional and one rest episode of chest pain; troponin negative, "
                "angiogram showed non-critical single-vessel CAD (mid-LAD 50%). Discharged on medical therapy "
                "with Aspirin, Atorvastatin 20 mg, Metoprolol and PRN nitrate.",
        pages=dis_pages,
        conditions=[{"name": "Coronary artery disease — chronic stable angina (single-vessel, non-critical LAD)",
                     "diagnosed_date": D("2024-06-24"), "severity": "moderate", "status": "active", "page": 1}],
        medications=[
            {**{k: v for k, v in _rx(**METFORMIN_SR_1000).items() if k not in ("pattern", "form")}, "row": False,
             "page": 3},
            {**{k: v for k, v in _rx(**GLIM_1).items() if k not in ("pattern", "form")}, "row": False, "page": 3},
            {**{k: v for k, v in _rx(**TELMA_40).items() if k not in ("pattern", "form")}, "row": False, "page": 3},
            {**{k: v for k, v in _rx(**ATORVA_20).items() if k not in ("pattern", "form")}, **started},
            {**{k: v for k, v in _rx(**ECOSPRIN_75).items() if k not in ("pattern", "form")}, **started},
            {**{k: v for k, v in _rx(**METOLAR_25).items() if k not in ("pattern", "form")}, **started},
            {**{k: v for k, v in _rx(**SORBITRATE).items() if k not in ("pattern", "form")}, **started},
            {**{k: v for k, v in _rx(**PANTOCID_40).items() if k not in ("pattern", "form")}, **started,
             "end_date": D("2024-07-27"), "active": False},
        ],
        labs=[
            {"test_name": "HbA1c", "value": "8.1", "unit": "%", "ref_range": "4.0-5.6", "flag": "high",
             "date": D("2024-06-23"), "page": 2},
            {"test_name": "FBS", "value": "149", "unit": "mg/dL", "ref_range": "70-100", "flag": "high",
             "date": D("2024-06-23"), "page": 2},
            {"test_name": "LDL", "value": "146", "unit": "mg/dL", "ref_range": "<100", "flag": "high",
             "date": D("2024-06-23"), "page": 2},
            {"test_name": "Creatinine", "value": "0.9", "unit": "mg/dL", "ref_range": "0.5-1.1", "flag": "normal",
             "date": D("2024-06-23"), "page": 2},
            {"test_name": "Hb", "value": "11.8", "unit": "g/dL", "ref_range": "12.0-15.5", "flag": "low",
             "date": D("2024-06-23"), "page": 2},
            {"test_name": "Troponin I", "value": "<0.01", "value_num": None, "unit": "ng/mL", "ref_range": "<0.04",
             "flag": "normal", "date": D("2024-06-22"), "page": 2},
        ],
        vitals=[{"kind": "bp", "value": "156/94", "unit": "mmHg", "date": adm},
                {"kind": "pulse", "value": "92", "unit": "/min", "date": adm},
                {"kind": "spo2", "value": "97", "unit": "%", "date": adm},
                {"kind": "weight", "value": "73.0", "unit": "kg", "date": adm}],
        encounter={"type": "ipd", "date": adm, "end_date": dis, "reason": "Chest pain on exertion and at rest",
                   "diagnosis": "Chronic stable angina — single-vessel non-critical CAD (mid-LAD 50%)",
                   "notes": "Troponin negative x2; Echo EF 58%; CAG: LAD 50%, LCx 30%, RCA normal — medical management.",
                   "advice": "Cardiac diet, graded walking after 2 weeks, carry glucose tablets (beta-blocker).",
                   "follow_up_date": D("2024-07-27"), "fee": 68400.0, "page": 3},
    )

    opd(LOTUS, "2024-07-27", "LHI/OP/24/07/3321",
        complaints="Post-discharge review. No chest pain since discharge. Walking 20 min/day on level ground.",
        history="Admitted 22-25 Jun 2024: chronic stable angina, CAG non-critical LAD 50%.",
        examination="HS normal. No pedal oedema. Chest clear.",
        vitals=[{"kind": "bp", "value": "134/84", "unit": "mmHg"}, {"kind": "pulse", "value": "64", "unit": "/min"},
                {"kind": "weight", "value": "72.0", "unit": "kg"}],
        investigations="ECG: sinus rhythm 64/min, resolved ST changes. LFT normal on statin.",
        diagnosis=["CAD — chronic stable angina, asymptomatic on OMT", "T2DM", "Hypertension — improving"],
        rx=[_rx(**ATORVA_20, duration="3 months"), _rx(**ECOSPRIN_75, duration="3 months"),
            _rx(**METOLAR_25, duration="3 months"), _rx(**SORBITRATE)],
        follow_up_text="Cardiology review in 6-9 months with lipid profile; endocrinology as scheduled.",
        enc={"reason": "Post-discharge cardiology review", "follow_up_date": D("2025-04-26"),
             "diagnosis": "Chronic stable angina — asymptomatic on medical therapy",
             "notes": "Pantocid stopped (4-week course complete). Continue Aspirin, Atorvastatin 20, Metoprolol XR 25.",
             "advice": "Increase walking to 30-40 min/day; cardiac rehabilitation sessions; low-salt diet."},
        summary="Post-discharge cardiology review: asymptomatic, BP 134/84, pulse 64; Pantoprazole stopped, "
                "anti-anginal and statin therapy continued.")

    opd(SANJEEVANI, "2024-10-19", "SDE/24/10/0642",
        complaints="Follow-up. Feels better since cardiac admission; no hypoglycaemia.",
        history="CAD (stable angina) diagnosed Jun-2024 at Lotus Heart Institute, now on Aspirin, Atorvastatin 20, "
                "Metoprolol XR 25.",
        examination="Feet: no ulcers, sensation intact. No oedema.",
        vitals=[{"kind": "bp", "value": "132/82", "unit": "mmHg"}, {"kind": "pulse", "value": "66", "unit": "/min"},
                {"kind": "weight", "value": "71.5", "unit": "kg"}],
        investigations="CityCare 15-Oct-2024: HbA1c 7.9 %, FBS 142, PPBS 205; LDL 96 mg/dL (target <70 for CAD); "
                       "creatinine 0.9.",
        diagnosis=["T2DM — HbA1c 7.9%, above target", "CAD on OMT; LDL above secondary-prevention target"],
        rx=[_rx(**METFORMIN_SR_1000, duration="3 months"), _rx(**GLIM_2, duration="3 months"),
            _rx(**TELMA_40, duration="3 months")],
        med_rows={"Amaryl (Glimepiride)2 mg": {"start_date": D("2024-10-19"), "active": True}},
        follow_up_text="3 months with HbA1c, FBS/PPBS, urine ACR.",
        enc={"reason": "Diabetes follow-up", "follow_up_date": D("2025-01-25"),
             "diagnosis": "T2DM above target (HbA1c 7.9%)",
             "notes": "Glimepiride increased 1 -> 2 mg OD. Hypoglycaemia symptoms explained (may be blunted by "
                      "Metoprolol). Letter to Dr. Meera Iyer re LDL 96 on Atorvastatin 20.",
             "advice": "Do not skip meals; carry glucose tablets; check CBG if sweating/tremor; diet as before."},
        summary="HbA1c 7.9%; Glimepiride increased to 2 mg OD with hypoglycaemia precautions; LDL 96 above CAD target.")

    opd(SANJEEVANI, "2025-01-25", "SDE/25/01/0119",
        complaints="Routine follow-up. No hypoglycaemic episodes. Walking daily.",
        history="Adherent to medicines; home BP 125-135/78-84.",
        examination="Feet normal. No neuropathy on monofilament.",
        vitals=[{"kind": "bp", "value": "130/80", "unit": "mmHg"}, {"kind": "pulse", "value": "64", "unit": "/min"},
                {"kind": "weight", "value": "71.0", "unit": "kg"}],
        investigations="CityCare 20-Jan-2025: HbA1c 7.6 %, FBS 136, PPBS 192; urine ACR 24 mg/g (normal).",
        diagnosis=["T2DM — improving (HbA1c 7.6%)", "Hypertension — controlled"],
        rx=[_rx(**METFORMIN_SR_1000, duration="3 months"), _rx(**GLIM_2, duration="3 months"),
            _rx(**TELMA_40, duration="3 months")],
        follow_up_text="3 months with HbA1c; annual eye check (fundus) due.",
        enc={"reason": "Diabetes follow-up", "follow_up_date": D("2025-04-26"),
             "diagnosis": "T2DM improving (HbA1c 7.6%); hypertension controlled",
             "notes": "Fundus examination advised at an ophthalmology centre (not yet done).",
             "advice": "Continue diet and daily walking; reduce evening snacks."},
        summary="HbA1c 7.6% improving, BP 130/80 controlled; no microalbuminuria. Medicines unchanged.")

    opd(LOTUS, "2025-04-26", "LHI/OP/25/04/2290",
        complaints="Cardiology review. No angina; walks 3 km daily without symptoms.",
        history="Chronic stable angina on OMT since Jun-2024.",
        examination="HS normal. BP controlled.",
        vitals=[{"kind": "bp", "value": "128/80", "unit": "mmHg"}, {"kind": "pulse", "value": "62", "unit": "/min"},
                {"kind": "weight", "value": "70.8", "unit": "kg"}],
        investigations="CityCare 21-Apr-2025: LDL 78 mg/dL, HbA1c 7.4 %, creatinine 1.0. ECG: sinus rhythm, no ischaemia.",
        diagnosis=["CAD — chronic stable angina, asymptomatic", "LDL 78 mg/dL — near target (<70)"],
        rx=[_rx(**ATORVA_20, duration="6 months"), _rx(**ECOSPRIN_75, duration="6 months"),
            _rx(**METOLAR_25, duration="6 months"), _rx(**SORBITRATE)],
        follow_up_text="6 months with lipid profile; consider Atorvastatin 40 mg if LDL remains > 70.",
        enc={"reason": "Cardiology follow-up", "follow_up_date": D("2025-10-25"),
             "diagnosis": "Chronic stable angina — asymptomatic; LDL near target",
             "notes": "Continue Atorvastatin 20 mg (patient prefers not to escalate); reinforce diet.",
             "advice": "Continue daily walking; Mediterranean-style Indian diet; annual flu vaccine."},
        summary="Cardiology review: asymptomatic, BP 128/80, LDL 78 near target; therapy unchanged.")

    opd(SANJEEVANI, "2025-10-18", "SDE/25/10/0703",
        complaints="Follow-up. 2-3 episodes in the last month of pre-lunch sweating, tremor and palpitations, "
                   "relieved by sugar; home CBG 62-68 mg/dL during episodes. Had skipped breakfast on 2 occasions.",
        history="On Glycomet-SR 1000 BD, Amaryl 2 mg OD, Telma 40, Atorva 20, Ecosprin 75, Metolar XR 25.",
        examination="Feet normal. No orthostatic drop.",
        vitals=[{"kind": "bp", "value": "126/78", "unit": "mmHg"}, {"kind": "pulse", "value": "62", "unit": "/min"},
                {"kind": "weight", "value": "70.5", "unit": "kg"}],
        investigations="CityCare 13-Oct-2025: HbA1c 7.1 %, FBS 118, PPBS 168; LDL 72; creatinine 1.0, eGFR 72, "
                       "urine ACR 28 mg/g.",
        diagnosis=["T2DM — near target (HbA1c 7.1%) with mild symptomatic hypoglycaemia on sulfonylurea",
                   "Hypertension — controlled", "CAD on OMT"],
        rx=[_rx(**METFORMIN_SR_1000, duration="3 months"), _rx(**GLIM_2, duration="3 months"),
            _rx(**TELMA_40, duration="3 months")],
        follow_up_text="Review in 4 weeks (or SOS) with home CBG log — plan to de-escalate Glimepiride if episodes "
                       "recur; HbA1c in 3 months.",
        enc={"reason": "Diabetes follow-up; pre-lunch hypoglycaemic symptoms", "follow_up_date": D("2025-11-15"),
             "diagnosis": "T2DM near target; mild symptomatic hypoglycaemia (Glimepiride + beta-blocker)",
             "notes": "Hypoglycaemia education given; family taught to give glucose. Consider switching "
                      "Glimepiride to a DPP-4 inhibitor if recurrent.",
             "advice": "Never skip breakfast; carry 15 g glucose; CBG before lunch daily and log; "
                       "avoid exercise on empty stomach."},
        summary="HbA1c 7.1% but 2-3 mild pre-lunch hypoglycaemic episodes on Glimepiride 2 mg; counselled, "
                "review in 4 weeks to consider de-escalation.")

    # ---- Pharmacy bills ------------------------------------------------------------
    def bill(when: str, bill_no: str, prescriber: str, items, summary: str):
        d = D(when)
        pages, net = pharmacy_bill(AROGYA, ananya, d, bill_no, prescriber, items)
        add_document(db, ananya, filename=f"Arogya_Medicals_Bill_{d:%d%b%Y}.txt", doc_type="pharmacy_bill", doc_date=d,
                     facility=AROGYA["name"], doctor=None, specialization=None, summary=summary, pages=pages,
                     encounter={"type": "pharmacy", "reason": "Pharmacy purchase", "fee": net,
                                "notes": f"Prescribed by {prescriber}", "doctor_name": prescriber,
                                "specialization": None})

    bill("2023-10-14", "AM/23-24/04412", menon, [
        ("Glycomet 500 mg tab (strip of 20) x9", "GL3091 / 08-25", 9, 360.0),
        ("Amaryl 1 mg tab (strip of 30) x3", "AM1120 / 11-25", 3, 435.0),
        ("Telma 40 mg tab (strip of 15) x6", "TL7781 / 06-26", 6, 1290.0),
    ], "3-month supply of Metformin, Glimepiride and Telmisartan.")
    bill("2024-06-25", "AM/24-25/01877", meera, [
        ("Atorva 20 mg tab (strip of 15) x2", "AT5530 / 03-26", 2, 412.0),
        ("Ecosprin 75 mg tab (strip of 14) x2", "EC2201 / 12-25", 2, 46.0),
        ("Metolar XR 25 mg tab (strip of 15) x2", "MX9012 / 01-26", 2, 236.0),
        ("Sorbitrate 5 mg tab (strip of 50) x1", "SB0045 / 10-26", 1, 58.0),
        ("Pantocid 40 mg tab (strip of 15) x2", "PC3317 / 02-26", 2, 298.0),
        ("Glycomet-SR 1000 mg tab (strip of 10) x6", "GS6650 / 09-25", 6, 690.0),
        ("Accu-Chek Active test strips (50)", "AC5512 / 05-25", 1, 1099.0),
    ], "Discharge medicines after cardiac admission plus glucometer strips.")
    bill("2025-01-25", "AM/24-25/06120", menon, [
        ("Glycomet-SR 1000 mg tab (strip of 10) x18", "GS7712 / 06-26", 18, 2070.0),
        ("Amaryl 2 mg tab (strip of 30) x3", "AM2290 / 08-26", 3, 705.0),
        ("Telma 40 mg tab (strip of 15) x6", "TL8820 / 10-26", 6, 1290.0),
    ], "3-month supply of diabetes and blood-pressure medicines.")
    bill("2025-10-18", "AM/25-26/03305", menon, [
        ("Glycomet-SR 1000 mg tab (strip of 10) x18", "GS8841 / 04-27", 18, 2070.0),
        ("Amaryl 2 mg tab (strip of 30) x3", "AM3302 / 06-27", 3, 705.0),
        ("Atorva 20 mg tab (strip of 15) x6", "AT6619 / 03-27", 6, 1236.0),
        ("Ecosprin 75 mg tab (strip of 14) x6", "EC3390 / 01-27", 6, 138.0),
        ("Metolar XR 25 mg tab (strip of 15) x6", "MX9930 / 02-27", 6, 708.0),
        ("Glucose tablets (Glucon-D tabs, 20)", "GD1102 / 12-26", 2, 90.0),
    ], "3-month supply of diabetes, cardiac medicines and glucose tablets.")


# ---------------------------------------------------------------------------
# Ravi Kumar — bronchial asthma (lightweight)
# ---------------------------------------------------------------------------

FORACORT = dict(drug="Foracort 200 Inhaler (Budesonide 200 mcg + Formoterol 6 mcg)", dosage="2 puffs",
                frequency="BD", pattern="2-0-2", instructions="with spacer; rinse mouth after use", form="",
                route="inhalation")
ASTHALIN = dict(drug="Asthalin Inhaler (Salbutamol 100 mcg)", dosage="2 puffs", frequency="SOS", pattern="SOS",
                instructions="for wheeze/breathlessness; max 8 puffs/day", form="", route="inhalation")
MONTAIR = dict(drug="Montair (Montelukast)", dosage="10 mg", frequency="HS", pattern="0-0-1", instructions="at bedtime")
WYSOLONE = dict(drug="Wysolone (Prednisolone)", dosage="30 mg", frequency="OD", pattern="1-0-0",
                instructions="after breakfast", duration="5 days")


def _seed_ravi(db: Session, ravi: User) -> None:
    H = ravi.patient_profile.height_cm

    def visit(clinic, when, opd_no, *, vitals, rx, rows, enc, summary, conditions=(), **note):
        d = D(when)
        pages = opd_note(clinic, ravi, d, opd_no, vitals=vitals, rx=rx, fee=clinic["fee"], height_cm=H,
                         advice=enc["advice"], follow_up=note.pop("follow_up_text"), **note)
        meds = []
        for m in rx:
            item = {k: v for k, v in m.items() if k not in ("pattern", "form")}
            item.update(rows.get(m["drug_name"], {"row": False}))
            meds.append(item)
        add_document(db, ravi, filename=f"{clinic['prefix']}_OPD_{d:%d%b%Y}.txt", doc_type="opd_note", doc_date=d,
                     facility=clinic["name"], doctor=clinic["doctor"], specialization=clinic["spec"], summary=summary,
                     pages=pages, conditions=conditions, medications=meds, vitals=vitals,
                     encounter={"type": "opd", "fee": clinic["fee"], **enc})

    visit(BREATHEWELL, "2025-01-11", "BWC/25/01/0082",
          complaints="Wheeze, chest tightness and night cough x 5 days after a viral cold; using Asthalin 6-8 times/day.",
          history="Bronchial asthma since childhood (diagnosed 2002), previously on Asthalin SOS only. "
                  "NSAIDs trigger bronchospasm. Mother asthmatic. Non-smoker.",
          examination="Bilateral expiratory wheeze, no crepitations. Speaking full sentences. PEFR 340 L/min (62% predicted).",
          vitals=[{"kind": "spo2", "value": "95", "unit": "%"}, {"kind": "pulse", "value": "102", "unit": "/min"},
                  {"kind": "bp", "value": "124/80", "unit": "mmHg"}, {"kind": "temp", "value": "99.1", "unit": "F"}],
          investigations="Nebulised with Levolin + Budecort in clinic: PEFR improved to 420 L/min.",
          diagnosis=["Acute exacerbation of bronchial asthma (moderate) following viral URTI",
                     "Moderate persistent asthma — inadequately controlled on reliever alone"],
          rx=[_rx(**FORACORT, duration="3 months"), _rx(**ASTHALIN), _rx(**MONTAIR, duration="1 month"), _rx(**WYSOLONE)],
          rows={FORACORT["drug"]: {"active": True}, ASTHALIN["drug"]: {"active": True},
                MONTAIR["drug"]: {"active": True},
                WYSOLONE["drug"]: {"active": False, "end_date": D("2025-01-15")}},
          conditions=[{"name": "Bronchial asthma (moderate persistent)", "diagnosed_date": D("2002-06-01"),
                       "severity": "moderate", "status": "active"}],
          follow_up_text="1 week with spirometry, AEC and total IgE.",
          enc={"reason": "Wheeze and breathlessness after viral cold", "follow_up_date": D("2025-01-18"),
               "diagnosis": "Acute exacerbation of moderate persistent asthma",
               "notes": "Started on ICS-LABA maintenance. Inhaler technique demonstrated with spacer.",
               "advice": "Avoid dust and cold air; strictly avoid Aspirin/NSAIDs (use Paracetamol); "
                         "written asthma action plan given."},
          summary="Acute asthma exacerbation after viral URTI; started Foracort 200 BD, Montair 10 mg, 5-day "
                  "Prednisolone and Asthalin SOS.")

    d = D("2025-01-18")
    tests = [
        {"test_name": "FEV1/FVC", "label": "FEV1/FVC ratio (pre-bronchodilator)", "value": "0.68", "unit": "ratio",
         "ref_range": ">0.70", "flag": "low"},
        {"test_name": "FEV1 reversibility", "label": "FEV1 post-bronchodilator change", "value": "16", "unit": "%",
         "ref_range": "<12 (significant if >=12% & 200 mL)", "flag": "high"},
        {"test_name": "Absolute eosinophil count", "label": "Absolute eosinophil count", "value": "620",
         "unit": "cells/uL", "ref_range": "40-440", "flag": "high"},
        {"test_name": "Total IgE", "label": "Serum total IgE", "value": "480", "unit": "IU/mL", "ref_range": "<100",
         "flag": "high"},
    ]
    pages = lab_report(NIRAMAYA_LAB, ravi, d, "NDL/2501/00341", BREATHEWELL["doctor"],
                       [("Spirometry & allergy markers", tests)], 3200)
    labs = [{k: v for k, v in t.items() if k != "label"} for t in tests]
    add_document(db, ravi, filename=f"Niramaya_Spirometry_IgE_{d:%d%b%Y}.txt", doc_type="lab_report", doc_date=d,
                 facility=NIRAMAYA_LAB["name"], doctor="Dr. P. Shenoy", specialization="Pathology",
                 summary="Obstructive spirometry (FEV1/FVC 0.68) with 16% bronchodilator reversibility; eosinophilia "
                         "(AEC 620) and raised total IgE 480 — consistent with allergic asthma.",
                 pages=pages, labs=labs,
                 encounter={"type": "diagnostic", "reason": "Spirometry and allergy work-up", "fee": 3200.0,
                            "doctor_name": None, "specialization": "Pathology"})

    visit(BREATHEWELL, "2025-07-05", "BWC/25/07/0641",
          complaints="Review. No night symptoms; reliever use < 2/week. Exercising (badminton) without symptoms.",
          history="On Foracort 200 BD and Montair 10 mg since Jan-2025.",
          examination="Chest clear. PEFR 510 L/min (93% predicted).",
          vitals=[{"kind": "spo2", "value": "98", "unit": "%"}, {"kind": "pulse", "value": "78", "unit": "/min"},
                  {"kind": "bp", "value": "118/76", "unit": "mmHg"}, {"kind": "weight", "value": "72", "unit": "kg"}],
          investigations="Spirometry (Niramaya, 18-Jan-2025): FEV1/FVC 0.68, reversibility 16%; AEC 620; IgE 480.",
          diagnosis=["Allergic bronchial asthma — well controlled (ACT score 23)"],
          rx=[_rx(**FORACORT, duration="6 months"), _rx(**ASTHALIN), _rx(**MONTAIR, duration="6 months")],
          rows={},
          follow_up_text="6 months, or SOS during monsoon flare.",
          enc={"reason": "Asthma control review", "follow_up_date": D("2026-01-10"),
               "diagnosis": "Allergic asthma — well controlled",
               "notes": "Continue maintenance; consider step-down after a further 3 months of control.",
               "advice": "Annual influenza vaccine; mite-proof pillow covers; continue to avoid NSAIDs."},
          summary="Asthma well controlled on Foracort 200 BD + Montair; PEFR 93% predicted. Continue therapy.")

    visit(BREATHEWELL, "2026-08-20", "BWC/26/08/0517",
          complaints="Monsoon flare x 4 days: night cough and wheeze; reliever 4-5 times/day for 3 days.",
          history="Well controlled until July; had stopped Montair on his own in May-2026.",
          examination="Scattered expiratory wheeze. PEFR 410 L/min (75% predicted).",
          vitals=[{"kind": "spo2", "value": "96", "unit": "%"}, {"kind": "pulse", "value": "96", "unit": "/min"},
                  {"kind": "bp", "value": "122/78", "unit": "mmHg"}],
          investigations="-",
          diagnosis=["Mild exacerbation of allergic asthma (seasonal)", "Influenza vaccine given today"],
          rx=[_rx(**{**FORACORT, "instructions": "with spacer; may take 1 extra puff (max 8/day) during flare"},
                  duration="3 months"),
              _rx(**ASTHALIN), _rx(**MONTAIR, duration="3 months")],
          rows={},
          follow_up_text="2 weeks if not settling; routine review in 3 months.",
          enc={"reason": "Seasonal asthma flare", "follow_up_date": D("2026-11-20"),
               "diagnosis": "Mild seasonal exacerbation of allergic asthma",
               "notes": "Montair restarted. Influenza vaccine (Vaxigrip Tetra 0.5 mL IM) administered.",
               "advice": "Restart Montair nightly; keep windows closed on damp days; follow asthma action plan."},
          summary="Mild monsoon asthma flare after stopping Montair; Montair restarted, Foracort continued, flu "
                  "vaccine given.")

    d = D("2026-08-20")
    pages, net = pharmacy_bill(NEEM, ravi, d, "NTP/26-27/02281", BREATHEWELL["doctor"], [
        ("Foracort 200 Inhaler (120 MD) x2", "FC2219 / 04-28", 2, 1196.0),
        ("Asthalin 100 Inhaler (200 MD) x1", "AS8812 / 01-28", 1, 168.0),
        ("Montair 10 mg tab (strip of 15) x6", "MT5521 / 07-28", 6, 1452.0),
        ("Zerostat VT spacer", "ZS1001 / -", 1, 340.0),
    ])
    add_document(db, ravi, filename=f"NeemTree_Pharmacy_Bill_{d:%d%b%Y}.txt", doc_type="pharmacy_bill", doc_date=d,
                 facility=NEEM["name"], doctor=None, specialization=None,
                 summary="3-month inhaler and Montelukast supply with spacer.", pages=pages,
                 encounter={"type": "pharmacy", "reason": "Pharmacy purchase", "fee": net,
                            "doctor_name": BREATHEWELL["doctor"], "specialization": None})

    visit(LOTUS, "2026-09-26", "LHI/OP/26/09/4410",
          complaints="Palpitations x 1 week, mostly after reliever inhaler use during the recent flare. No syncope, "
                     "no chest pain.",
          history="Allergic asthma on Foracort 200 BD + Montair; Asthalin use was 4-5/day in late August, now 1-2/day.",
          examination="HS normal, no murmur. Chest: occasional wheeze.",
          vitals=[{"kind": "bp", "value": "120/78", "unit": "mmHg"}, {"kind": "pulse", "value": "92", "unit": "/min"},
                  {"kind": "spo2", "value": "97", "unit": "%"}, {"kind": "weight", "value": "72.5", "unit": "kg"}],
          investigations="ECG: sinus tachycardia 98/min, normal intervals, no arrhythmia. TSH 1.9 (normal).",
          diagnosis=["Palpitations — sinus tachycardia related to beta-agonist overuse; no structural heart disease "
                     "suspected", "Allergic asthma"],
          rx=[_rx(**FORACORT), _rx(**{**ASTHALIN, "instructions": "SOS only; if needed > 3 times/week, review"})],
          rows={},
          follow_up_text="SOS. 24-h Holter only if palpitations persist after reliever use normalises.",
          enc={"reason": "Palpitations after frequent reliever inhaler use", "follow_up_date": None,
               "diagnosis": "Sinus tachycardia (beta-agonist related); no arrhythmia",
               "notes": "Reassured. Avoid caffeine excess. Beta-blockers avoided (asthma). Shared records with "
                        "consent for continuity.",
               "advice": "Limit Asthalin; follow pulmonologist plan; reduce coffee to 1 cup/day."},
          summary="Palpitations from salbutamol overuse; ECG sinus tachycardia only, no arrhythmia; reassured, "
                  "reliever use to be minimised.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

SEED_GRANT_OTP = "482913"


def _seeded_grant(db: Session, ravi: User, meera: User) -> ConsentGrant:
    created = utcnow() - timedelta(minutes=10)
    redeemed = created + timedelta(minutes=2)
    grant = ConsentGrant(patient_id=ravi.id, doctor_id=meera.id, otp=SEED_GRANT_OTP, created_at=created,
                         otp_expires_at=created + timedelta(minutes=10), access_minutes=10080,
                         expires_at=redeemed + timedelta(minutes=10080), scope="read_write")
    db.add(grant)
    db.flush()
    return grant


def refresh_seeded_grant(db: Session) -> ConsentGrant | None:
    """Startup hook: if the demo Ravi -> Dr. Meera grant has lapsed (expired, not revoked), insert a fresh
    7-day seeded grant so the doctor demo keeps working on long-lived deployments. Returns the new grant."""
    ravi = db.scalar(select(User).where(User.email == "ravi@demo.in", User.role == "patient"))
    meera = db.scalar(select(User).where(User.email == "meera@demo.in", User.role == "doctor"))
    if ravi is None or meera is None:
        return None
    latest = db.scalar(select(ConsentGrant).where(ConsentGrant.patient_id == ravi.id,
                                                  ConsentGrant.doctor_id == meera.id)
                       .order_by(ConsentGrant.id.desc()).limit(1))
    if latest is None or latest.revoked_at is not None:
        return None  # never seeded, or the patient deliberately revoked it
    if latest.expires_at is not None and latest.expires_at > utcnow():
        return None  # still active
    with atomic(db):  # new grant + audit row commit together
        grant = _seeded_grant(db, ravi, meera)
        log_event(db, None, "GRANT_REFRESHED_SEED", patient_id=ravi.id,
                  detail={"grant_id": grant.id, "previous_grant_id": latest.id, "doctor_id": meera.id,
                          "access_minutes": 10080, "scope": "read_write", "seeded": True})
    return grant


def seed_demo(db: Session) -> dict | None:
    """Populate the demo dataset. No-op (returns None) if any user already exists."""
    if (db.scalar(select(func.count()).select_from(User)) or 0) > 0:
        return None
    # All-or-nothing: demo data, seed audit rows and the demo grant commit in ONE transaction, so a crash
    # mid-seed leaves an empty DB that is simply re-seeded on the next start.
    with atomic(db):
        users = _create_users(db)
        _seed_ananya(db, users["ananya"])
        _seed_ravi(db, users["ravi"])
        db.flush()
        log_event(db, None, "SEED_DEMO", detail={"users": [u.email for u in users.values()]})
        # Ravi -> Dr. Meera: active 7-day read_write grant (Ananya has none, so the demo shows the OTP flow).
        grant = _seeded_grant(db, users["ravi"], users["meera"])
        log_event(db, users["ravi"], "GRANT_CREATED", patient_id=users["ravi"].id,
                  detail={"grant_id": grant.id, "access_minutes": 10080, "scope": "read_write", "seeded": True})
        log_event(db, users["meera"], "GRANT_REDEEMED", patient_id=users["ravi"].id,
                  detail={"grant_id": grant.id, "scope": "read_write", "seeded": True})
    return {k: u.id for k, u in users.items()}
