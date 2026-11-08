import tempfile
from functools import lru_cache
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from paddleocr import PaddleOCR

app = FastAPI()
lock = Lock()


@lru_cache(maxsize=1)
def engine():
    return PaddleOCR(
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name="en_PP-OCRv5_mobile_rec",
        use_doc_orientation_classify=True, use_doc_unwarping=False,
        use_textline_orientation=False, enable_mkldnn=False, cpu_threads=2,
    )


def read_image(raw):
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "specimen.png"
        target.write_bytes(raw)
        with lock:
            result = list(engine().predict(str(target)))[0].json["res"]
        return [{"text": text, "confidence": float(score), "box": [int(value) for value in box]}
                for text, score, box in zip(result["rec_texts"], result["rec_scores"], result["rec_boxes"], strict=True)]


@app.post("/extract")
async def extract(request: Request):
    from starlette.concurrency import run_in_threadpool

    content = await request.body()
    if not content or len(content) > 6_000_000:
        raise HTTPException(413, detail="invalid_ocr_input")
    return await run_in_threadpool(read_image, content)
