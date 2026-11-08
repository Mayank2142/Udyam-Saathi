"""Rerun the supplied synthetic specimens through the sandbox OCR and rules."""
import json
import os
import time
from pathlib import Path

from app.activation import DOC_FILES
from app.verification import check_document, extract, specimen_image


BUSINESSES = {
    "K01": "Vindhya Tours & Travels", "K02": "Harmony Dental Care", "K03": "Ganesh Woodcraft",
    "K04": "Saffron Dental Care LLP", "K05": "Balaji Realty LLP", "K06": "Saffron Unisex Salon",
    "K07": "Neelgiri Physio Clinic", "K08": "Green Leaf Legal Associates", "K09": "Kaveri Insurance Advisors",
    "K10": "Kaveri Stays LLP", "K11": "Trinity Bake House Pvt Ltd", "K12": "Star Electronics",
}


def main():
    directory = Path(os.environ["SAATHI_DATA_DIR"])
    kinds = {filename: kind for kind, filename in DOC_FILES.items()}
    rows = []
    for file in sorted((directory / "kyc_documents").glob("K??_*.png")):
        case, filename = file.stem.split("_", 1)
        kind = kinds[filename]
        start = time.monotonic()
        image = specimen_image(file.read_bytes(), kind=kind)
        decision = check_document(kind, extract(image), BUSINESSES[case], directory)
        row = {"sample": case, "kind": kind, "status": decision["status"],
               "reason": decision["reason"], "seconds": round(time.monotonic() - start, 2)}
        rows.append(row)
        print(case, kind, row["status"], row["reason"], flush=True)
        if case == "K07" and kind == "gst":
            assert (row["status"], row["reason"]) == ("action", "gst_name_mismatch"), row
        if case == "K11" and kind == "gst":
            assert (row["status"], row["reason"]) == ("pass", "mock_match"), row
    assert len(rows) == 47
    print("RESULT=" + json.dumps({"rows": rows}, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
