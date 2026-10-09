# Sample documents

All files are **synthetic** and belong to demo patient **Ananya Sharma** (DHID `DH-8923-4102-9981`, F, DOB 1968-04-12; T2DM since 2019, hypertension since 2021). They describe NEW events in 2025-2026, so uploading them adds to the seeded history instead of duplicating it. Regenerate with `python samples/make_samples.py` (needs pillow, reportlab, python-docx, openpyxl; ffmpeg on PATH for the mp4).

| File | Demonstrates |
|---|---|
| `prescription_handwritten.jpg` | Handwriting-style image (blue ink, slight rotation, noise): Dr. R. Menon, 2026-09-15; Glycomet 500 (BD), Januvia 100 (OD), Telma 40 (OD), Atorva 20 (HS), Ecosprin 75 (OD); follow-up ~15/12/26; fee 700 INR. OCR / vision path. |
| `scanned_prescription.pdf` | Image-only PDF (no text layer) of the same prescription: pages must be rendered and OCR'd / sent to vision. |
| `lab_report.pdf` | 2-page text PDF from Sunrise Diagnostics, Pune (12/09/2026). Page 1: HbA1c 7.0 %, FBS 128, PPBS 182. Page 2: lipid profile, creatinine, TSH with reference ranges and H/L flags. Page citations. |
| `discharge_summary.docx` | Short admission 2025-11-08 to 11-10 for a hypoglycaemia episode (glimepiride stopped, sitagliptin added); Word tables for investigations and discharge medications. |
| `lab_history.xlsx` | Sheets `HbA1c` (2024-2026) and `Lipids` (2024-2026): multi-sheet tabular extraction and trends. |
| `pharmacy_bill.jpg` | Printed-receipt style bill with items, qty, MRP and totals in INR. |
| `followup_video.mp4` | ~8 s slideshow of a lab report image then the prescription image: video frame extraction + OCR. |
