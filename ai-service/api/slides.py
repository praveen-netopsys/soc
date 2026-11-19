import os
import shutil
import tempfile
import json
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from services.slide_renderer import process_presentation, TARGET_WIDTH, TARGET_HEIGHT

router = APIRouter(prefix="/api/slides", tags=["Slides"])

@router.get("/health")
def slides_health():
    return {
        "status": "online",
        "renderer": "Spire.Presentation + python-pptx + pypdfium2",
        "targetResolution": f"{TARGET_WIDTH}x{TARGET_HEIGHT} (1080p Lanczos)"
    }

@router.post("/render")
async def render_slides(
    file: UploadFile = File(...),
    output_dir: str = Form(None)
):
    """
    Renders presentation slides to 1080p PNG images using Spire.Presentation
    and extracts structured manifest using python-pptx.
    """
    filename = file.filename or "presentation.pptx"
    ext = os.path.splitext(filename)[1].lower()

    if ext not in ('.pptx', '.ppt', '.pdf', '.docx', '.doc'):
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{ext}'. Must be .pptx, .ppt, .pdf, or .docx."
        )

    tmp_dir = tempfile.mkdtemp(prefix="sb_slides_")
    tmp_file = os.path.join(tmp_dir, filename)

    try:
        with open(tmp_file, "wb") as f:
            content = await file.read()
            f.write(content)

        target_out = output_dir if output_dir else os.path.join(tmp_dir, "output")
        os.makedirs(target_out, exist_ok=True)

        manifest = process_presentation(tmp_file, target_out)

        # Attach base64 dataUrl or read image buffers if needed
        import base64
        for slide in manifest.get("slides", []):
            img_path = slide.get("imagePath")
            if img_path and os.path.exists(img_path):
                with open(img_path, "rb") as im_f:
                    img_bytes = im_f.read()
                    slide["dataUrl"] = f"data:image/png;base64,{base64.b64encode(img_bytes).decode('ascii')}"
                    slide["sizeBytes"] = len(img_bytes)

        return JSONResponse(content=manifest)
    except Exception as e:
        print(f"[Slides API] Error processing {filename}: {e}")
        raise HTTPException(status_code=500, detail=f"Slide rendering failed: {str(e)}")
    finally:
        # Keep output_dir if explicitly provided by caller, otherwise tmp_dir will be cleaned if caller consumed dataUrl
        if not output_dir and os.path.exists(tmp_dir):
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

