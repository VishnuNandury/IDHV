"""Task 2: offline (regex + optional OCR) digitiser."""
from __future__ import annotations

import io

from PIL import Image

from app.ai import offline_extract
from app.ai.offline_extract import ocr_image, offline_digitise
from app.schemas import ExtractedContent

LAB_TEXT = ("HbA1c 7.8 % 4.0-5.6\nFasting Blood Sugar 142 mg/dL 70-100\nBP 140/90\nTab Metformin 500 mg BD\n"
            "Consultation fee Rs. 600\nDate: 12/03/2025")


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(buf, format="PNG")
    return buf.getvalue()


def _lab(result, name):
    return next(lab for lab in result.lab_results if lab.test_name == name)


def test_brief_lab_text():
    r = offline_digitise(ExtractedContent(text=LAB_TEXT), "labs.txt")
    hba1c = _lab(r, "HbA1c")
    assert hba1c.value == "7.8" and hba1c.unit == "%" and hba1c.ref_range == "4.0-5.6" and hba1c.flag == "high"
    fbs = _lab(r, "FBS")
    assert fbs.value == "142" and fbs.unit == "mg/dL" and fbs.flag == "high"
    assert any(v.kind == "bp" and v.value == "140/90" for v in r.vitals)
    med = next(m for m in r.medications if "Metformin" in (m.drug_name or ""))
    assert med.drug_name == "Metformin" and med.dosage == "500 mg" and med.frequency == "BD"
    assert r.encounter is not None and r.encounter.fee == 600
    assert r.document_date == "2025-03-12"
    assert r.confidence <= 0.5
    assert r.warnings  # always flags that it was offline


def test_dates_are_not_mistaken_for_bp():
    r = offline_digitise(ExtractedContent(text="Date: 12/03/2025\nReview on 15/04/2025"), "x.txt")
    assert r.vitals == []


def test_multiline_pdf_layout_with_pages_and_flags():
    text = ("[Page 1]\nSunrise Diagnostics, Pune\nCollected\n12/09/2026 08:10\nReferred by\nDr. R. Menon\n"
            "HbA1c (Glycated Hb)\n7.0\n%\n4.0 - 5.6\nH\n"
            "[Page 2]\nHDL Cholesterol\n44\nmg/dL\n> 50\nL\nTotal Cholesterol\n196\nmg/dL\n< 200\n"
            "Serum Creatinine\n0.9\nmg/dL\n0.5 - 1.1\n")
    r = offline_digitise(ExtractedContent(text=text, pages=2), "lab_report.pdf")
    assert r.doc_type == "lab_report"
    assert r.document_date == "2026-09-12"
    assert r.facility == "Sunrise Diagnostics, Pune"
    assert r.doctor_name == "Dr. R. Menon"
    h = _lab(r, "HbA1c")
    assert (h.value, h.flag, h.page) == ("7.0", "high", 1)
    hdl = _lab(r, "HDL")
    assert (hdl.value, hdl.flag, hdl.page) == ("44", "low", 2)
    assert _lab(r, "Total Cholesterol").flag == "normal"
    assert _lab(r, "Creatinine").value == "0.9"
    assert r.encounter.type == "diagnostic"


def test_tabular_header_rows():
    text = "[Sheet: Lipids]\nDate\tTotal Cholesterol\tLDL\tHDL\tUnit\n2024-02-10\t218\t142\t42\tmg/dL\n"
    r = offline_digitise(ExtractedContent(text=text), "lab_history.xlsx")
    ldl = _lab(r, "LDL")
    assert ldl.value == "142" and ldl.date == "2024-02-10" and ldl.unit == "mg/dL"
    assert {lab.test_name for lab in r.lab_results} >= {"Total Cholesterol", "LDL", "HDL"}


def test_prescription_and_discharge_classification():
    rx = offline_digitise(ExtractedContent(text="Rx\nTab Glycomet 500 mg BD x 30 days\nCap Pantocid 40 mg OD\n"
                                                "Dr. R. Menon\nDate 15/09/2026\nFee: 700 INR"), "rx.txt")
    assert rx.doc_type == "prescription"
    assert [m.frequency for m in rx.medications] == ["BD", "OD"]
    assert rx.medications[0].duration == "30 days"
    assert rx.medications[1].route == "oral"
    assert rx.encounter.type == "opd" and rx.encounter.fee == 700

    ds = offline_digitise(ExtractedContent(text="DISCHARGE SUMMARY\nDate of admission\t2025-11-08\n"
                                                "Date of discharge\t2025-11-10\n"
                                                "Diagnosis\tHypoglycaemia episode; Type 2 diabetes mellitus; Hypertension\n"
                                                "Total hospital bill: ₹18,450."), "ds.docx")
    assert ds.doc_type == "discharge_summary"
    assert ds.encounter.type == "ipd"
    assert ds.encounter.date == "2025-11-08" and ds.encounter.end_date == "2025-11-10"
    assert ds.encounter.fee == 18450
    assert {c.name for c in ds.conditions} >= {"Type 2 diabetes mellitus", "Hypertension"}


def test_ocr_image_without_tesseract_returns_empty(monkeypatch):
    import pytesseract

    def boom(*a, **k):
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "image_to_string", boom)
    assert ocr_image(_png()) == ""
    r = offline_digitise(ExtractedContent(images=[(_png(), 1)]), "scan.jpg")
    assert any("OCR" in w for w in r.warnings)


def test_ocr_text_flows_into_offline_digitise(monkeypatch):
    import pytesseract

    monkeypatch.setattr(pytesseract, "image_to_string", lambda img, *a, **k: "HbA1c 8.1 % 4.0-5.6\nBP 150/95 mmHg")
    assert "HbA1c" in ocr_image(_png())
    r = offline_digitise(ExtractedContent(images=[(_png(), 3)]), "photo.jpg")
    lab = _lab(r, "HbA1c")
    assert lab.value == "8.1" and lab.page == 3
    assert any(v.value == "150/95" for v in r.vitals)


def test_garbage_bytes_never_crash():
    assert offline_extract.ocr_image(b"not a png") == ""
    r = offline_digitise(ExtractedContent(text=""), "empty.txt")
    assert r.doc_type == "other" and r.warnings


# ---- fix round 1 -------------------------------------------------------------

def test_reference_ranges_are_not_dates():
    r = offline_digitise(ExtractedContent(text="Hb 14.1 g/dL 13.5-17.5\nCalcium 9.1 mg/dL 8.5-10.5\nDate: 12/03/2025"),
                         "cbc.txt")
    hb = _lab(r, "Hb")
    assert hb.ref_range == "13.5-17.5" and hb.flag == "normal"
    assert hb.date == "2025-03-12"
    assert r.document_date == "2025-03-12"
    assert all(lab.date == "2025-03-12" for lab in r.lab_results)


def test_date_separator_rules():
    from app.ai.offline_extract import _dates_in

    assert [d.isoformat() for d in _dates_in("12/03/2025 12-03-2025 12.03.2025 2025-03-12 12/03/25")] == \
        ["2025-03-12"] * 5
    assert _dates_in("13.5-17.5") == [] and _dates_in("12-03/2025") == []
    assert _dates_in("12.03.25") == [] and _dates_in("12-03-25") == []  # 2-digit years only with '/'


def test_row_date_ignores_two_digit_year():
    r = offline_digitise(ExtractedContent(text="Date: 01/02/2024\nHbA1c 7.1 % 4.0-5.6 on 03/04/24"), "x.txt")
    assert _lab(r, "HbA1c").date == "2024-02-01"


def test_header_row_with_alphanumeric_tests():
    text = "Date\tT3\tT4\tTSH\n2025-01-01\t1.2\t8.1\t3.2\n"
    r = offline_digitise(ExtractedContent(text=text), "thyroid.xlsx")
    assert (_lab(r, "T3").value, _lab(r, "T4").value, _lab(r, "TSH").value) == ("1.2", "8.1", "3.2")
    assert _lab(r, "T4").date == "2025-01-01"
