import csv
import hashlib
import io
import os
import re
import warnings
from datetime import date
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

CLOCK = date(2026, 7, 15)
BUCKET = "synthetic-kyc"
GST = re.compile(r"\b\d{2}[A-Z]{3}X[A-Z]\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b")
GST_NAME_CHECK_VERSION = 1
UDYAM = re.compile(r"\bUDYAM-[A-Z]{2}-00-\d{7}\b")
SIGNATORY = re.compile(r"\bSYN-ID-\d{6}\b")
ISO_DATE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
Image.MAX_IMAGE_PIXELS = 10_000_000


def norm(text: str) -> str:
    return "".join(char for char in text.casefold() if char.isalnum())


@lru_cache(maxsize=1)
def specimens(directory: Path):
    result = {}
    for path in sorted((directory / "kyc_documents").glob("K??_*.png")):
        result[hashlib.sha256(path.read_bytes()).hexdigest()] = path
    return result


def specimen_image(data: bytes, *, kind: str):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data), formats=["PNG", "JPEG"]) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                if kind == "udyam":
                    # The supplied certificates overlay pale red type on dark fields.
                    # Remove those colored pixels from the OCR copy only.
                    red, green, _ = image.split()
                    red_tint = ImageChops.subtract(red, green).point(lambda x: 255 if x > 17 else 0)
                    pale = green.point(lambda x: 255 if x > 105 else 0)
                    mask = ImageChops.multiply(red_tint, pale)
                    image = Image.composite(Image.new("RGB", image.size, "white"), image, mask)
                image.thumbnail((1500, 1700))
                if kind == "gst":
                    # Preserve dark GST text while suppressing the pale red watermark.
                    # The source specimen is untouched.
                    image = image.getchannel("R").point(lambda value: 0 if value < 128 else 255).convert("RGB")
                buffer = io.BytesIO()
                image.save(buffer, format="PNG")
                return buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("invalid_specimen") from error


def corrected_bill():
    image = Image.new("RGB", (1050, 690), "#fffefa")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 45, 1010, 645), outline="#172039", width=3)
    title, body = ImageFont.load_default(size=34), ImageFont.load_default(size=27)
    draw.text((68, 78), "Synthetic electricity bill", font=title, fill="#172039")
    draw.text((68, 144), "Team-created replacement for K11 · Exercise only", font=body, fill="#8b2820")
    draw.text((68, 235), "Billed to: Trinity Bake House Pvt Ltd", font=body, fill="black")
    draw.text((68, 300), "Supply address: Unit 188, Industrial Area Phase 2", font=body, fill="black")
    draw.text((68, 357), "Ludhiana, Punjab", font=body, fill="black")
    draw.text((68, 440), "Bill date: 2026-06-18", font=body, fill="black")
    draw.text((68, 565), "SYNTHETIC SPECIMEN · NOT A VALID DOCUMENT", font=body, fill="#8b2820")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@lru_cache(maxsize=1)
def s3():
    client = boto3.client("s3", endpoint_url=os.environ["SAATHI_S3_URL"], region_name="us-east-1",
                          aws_access_key_id=os.environ["SAATHI_S3_USER"],
                          aws_secret_access_key=os.environ["SAATHI_S3_PASSWORD"],
                          config=Config(connect_timeout=4, read_timeout=5, retries={"max_attempts": 1}))
    try:
        client.head_bucket(Bucket=BUCKET)
    except ClientError as error:
        if str(error.response["Error"]["Code"]) not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        try:
            client.create_bucket(Bucket=BUCKET)
        except ClientError as create_error:
            if create_error.response["Error"]["Code"] != "BucketAlreadyOwnedByYou":
                raise
    client.put_bucket_lifecycle_configuration(Bucket=BUCKET, LifecycleConfiguration={
        "Rules": [{"ID": "expire-temp", "Filter": {"Prefix": "temp/"}, "Status": "Enabled",
                   "Expiration": {"Days": 1}}],
    })
    return client


def extract(image: bytes):
    client = s3()
    key = f"temp/{uuid4()}"
    cipher, nonce = AESGCM.generate_key(bit_length=256), os.urandom(12)
    aes = AESGCM(cipher)
    client.put_object(Bucket=BUCKET, Key=key, Body=nonce + aes.encrypt(nonce, image, key.encode()))
    try:
        with client.get_object(Bucket=BUCKET, Key=key)["Body"] as stream:
            encrypted = stream.read()
        plain = aes.decrypt(encrypted[:12], encrypted[12:], key.encode())
        return httpx.post(os.environ["OCR_URL"], content=plain, timeout=150).raise_for_status().json()
    finally:
        client.delete_object(Bucket=BUCKET, Key=key)


@lru_cache(maxsize=4)
def registry(directory: Path, kind: str):
    filename, column = ("mock_gstin_registry.csv", "gstin") if kind == "gst" else (
        "mock_udyam_registry.csv", "udyam_number",
    )
    with (directory / filename).open(encoding="utf-8-sig", newline="") as handle:
        return {row[column].strip().upper(): row for row in csv.DictReader(handle)}


def gst_checksum(value: str):
    digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    subtotal = 0
    for offset, char in enumerate(reversed(value[:-1])):
        item = digits.index(char) * (2 if offset % 2 == 0 else 1)
        subtotal += item // 36 + item % 36
    return digits[-subtotal % 36] == value[-1]


def gst_printed_names(lines, number):
    # The supplied certificates place labels on the left and values on the right.
    located = [(line["text"], line["box"]) for line in lines if len(line.get("box", ())) == 4]
    identifiers = [box for text, box in located if text.strip().upper() == number]
    labels = [box for text, box in located if norm(text) == "gstin"]
    if len(identifiers) != 1 or len(labels) != 1:
        return None

    def center_y(box):
        return (box[1] + box[3]) / 2

    def center_x(box):
        return (box[0] + box[2]) / 2

    label, identifier = labels[0], identifiers[0]
    gap = identifier[0] - label[0]
    if gap <= 0 or center_x(identifier) <= center_x(label):
        return None
    column = label[0] + gap / 2
    slope = (center_y(identifier) - center_y(label)) / (center_x(identifier) - center_x(label))

    def row_y(box):
        return center_y(box) - slope * center_x(box)

    names = []
    for field in ("legal", "trade"):
        anchors = [box for text, box in located if box[0] < column and norm(text) in (field, field + "name")]
        if len(anchors) != 1:
            return None
        anchor = anchors[0]
        tolerance = (anchor[3] - anchor[1]) * 0.55
        parts = sorted((box[0], text) for text, box in located
                       if box[0] >= column and abs(row_y(box) - row_y(anchor)) <= tolerance)
        name = norm(" ".join(text for _, text in parts))
        if not name:
            return None
        names.append(name)
    return tuple(names)


def check_document(kind: str, lines: list[dict], business: str, directory: Path):
    checked = [row for row in lines if row.get("confidence", 0) >= .75]
    text = " ".join(row["text"] for row in checked)
    upper = re.sub(r"\s*-\s*", "-", text.upper().replace("–", "-"))
    label = norm(business)
    result = {"status": "action", "reason": "recapture", "source": "PaddleOCR",
              "checked_on": CLOCK.isoformat()}
    if len(label) < 3 or not checked:
        return result
    if kind in {"gst", "udyam"}:
        pattern = GST if kind == "gst" else UDYAM
        matches = set(pattern.findall(upper))
        if len(matches) != 1:
            return result
        number = matches.pop()
        result["masked_id"] = "••••" + number[-4:]
        if kind == "gst" and not gst_checksum(number):
            return {**result, "reason": "invalid_identifier"}
        row = registry(directory, kind).get(number)
        if not row:
            return {**result, "reason": "registry_missing"}
        if kind == "gst" and row["status"].casefold() != "active":
            return {**result, "reason": "gst_inactive"}
        if kind == "gst":
            names = tuple(norm(row[field]) for field in ("legal_name", "trade_name"))
            printed = gst_printed_names(checked, number)
            if not printed:
                return result
            if printed != names:
                return {**result, "reason": "gst_name_mismatch"}
            if label not in names:
                return {**result, "reason": "business_mismatch"}
            return {**result, "status": "pass", "reason": "mock_match", "check_version": GST_NAME_CHECK_VERSION}
        if label != norm(row["enterprise_name"]) or label not in norm(text):
            return {**result, "reason": "business_mismatch"}
        return {**result, "status": "pass", "reason": "mock_match"}

    if kind == "signatory":
        matches = set(SIGNATORY.findall(upper))
        if len(matches) != 1 or "VALID TILL" not in upper:
            return result
        result["masked_id"] = "••••" + matches.pop()[-4:]
    elif "BILL DATE" not in upper:
        return result
    dates = set(ISO_DATE.findall(upper))
    if len(dates) != 1:
        return {**result, "reason": "unclear_date"}
    try:
        extracted = date.fromisoformat(dates.pop())
    except ValueError:
        return {**result, "reason": "unclear_date"}
    result["date"] = extracted.isoformat()
    if kind == "address":
        age = (CLOCK - extracted).days
        result["age_days"] = age
        if not 0 <= age <= 90:
            return {**result, "reason": "stale_proof"}
    elif extracted < CLOCK:
        return {**result, "reason": "expired_id"}
    if label not in norm(text):
        return {**result, "reason": "business_mismatch"}
    return {**result, "status": "pass", "reason": "exercise_checks_passed"}
