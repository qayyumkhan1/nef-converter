import os
import uvicorn  
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask


app = FastAPI(title="PDFTools Converter API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://YOUR-FRONTEND-DOMAIN.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_FILE_SIZE = 50 * 1024 * 1024
TIMEOUT = 60


# =========================================================
# Helpers
# =========================================================

def cleanup(path: str):
    shutil.rmtree(path, ignore_errors=True)


async def save_upload(file: UploadFile, path: Path):
    size = 0

    with open(path, "wb") as output:
        while True:
            chunk = await file.read(1024 * 1024)

            if not chunk:
                break

            size += len(chunk)

            if size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail="Maximum file size is 50 MB."
                )

            output.write(chunk)


def safe_name(filename: str, extension: str):
    stem = Path(filename).stem

    cleaned = "".join(
        c for c in stem
        if c.isalnum() or c in ("-", "_", " ")
    ).strip()

    return f"{cleaned or 'converted'}{extension}"


def run_command(command):
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail="Conversion timed out."
        )
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Required converter is not installed: {e.filename}"
        )

    if result.returncode != 0:
        error = result.stderr.strip()

        raise HTTPException(
            status_code=422,
            detail=error or "Conversion failed."
        )

    return result


# =========================================================
# 1. PUBLISHER (.PUB) -> PDF
# =========================================================

@app.post("/convert-pub")
async def convert_pub(file: UploadFile = File(...)):

    filename = file.filename or ""

    if not filename.lower().endswith(".pub"):
        raise HTTPException(
            status_code=400,
            detail="Only .pub files are allowed."
        )

    workdir = Path(
        tempfile.mkdtemp(
            prefix=f"pub_{uuid.uuid4().hex}_"
        )
    )

    try:

        input_file = workdir / "input.pub"

        await save_upload(file, input_file)

        if input_file.stat().st_size == 0:
            raise HTTPException(
                status_code=400,
                detail="The uploaded file is empty."
            )

        # Isolated LibreOffice profile
        profile = workdir / "lo-profile"
        profile.mkdir()

        command = [
            "soffice",
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            "--norestore",
            f"-env:UserInstallation={profile.as_uri()}",
            "--convert-to",
            "pdf:draw_pdf_Export",
            "--outdir",
            str(workdir),
            str(input_file),
        ]

        run_command(command)

        output = workdir / "input.pdf"

        if not output.exists():
            raise HTTPException(
                status_code=422,
                detail=(
                    "LibreOffice could not produce a PDF from "
                    "this Publisher file."
                )
            )

        download_name = safe_name(filename, ".pdf")

        return FileResponse(
            str(output),
            media_type="application/pdf",
            filename=download_name,
            background=BackgroundTask(
                cleanup,
                str(workdir)
            ),
        )

    except HTTPException:
        cleanup(str(workdir))
        raise

    except Exception:
        cleanup(str(workdir))

        raise HTTPException(
            status_code=500,
            detail="Publisher conversion failed."
        )

    finally:
        await file.close()


# =========================================================
# 2. NEF -> PDF
# =========================================================

@app.post("/convert-nef")
async def convert_nef(file: UploadFile = File(...)):

    filename = file.filename or ""

    if not filename.lower().endswith(".nef"):
        raise HTTPException(
            status_code=400,
            detail="Only Nikon .nef files are allowed."
        )

    workdir = Path(
        tempfile.mkdtemp(
            prefix=f"nef_{uuid.uuid4().hex}_"
        )
    )

    try:

        input_file = workdir / "input.nef"
        ppm_file = workdir / "image.ppm"
        output_file = workdir / "output.pdf"

        await save_upload(file, input_file)

        if input_file.stat().st_size == 0:
            raise HTTPException(
                status_code=400,
                detail="The uploaded file is empty."
            )

        # -------------------------------------------------
        # NEF -> PPM
        # -------------------------------------------------

        with open(ppm_file, "wb") as ppm:

            try:
                result = subprocess.run(
                    [
                        "dcraw",
                        "-6",
                        "-w",
                        "-q",
                        "3",
                        "-c",
                        str(input_file),
                    ],
                    stdout=ppm,
                    stderr=subprocess.PIPE,
                    text=False,
                    timeout=TIMEOUT,
                    check=False,
                )

            except subprocess.TimeoutExpired:
                raise HTTPException(
                    status_code=504,
                    detail="NEF conversion timed out."
                )

            except FileNotFoundError:
                raise HTTPException(
                    status_code=500,
                    detail="dcraw is not installed."
                )

        if result.returncode != 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    result.stderr.decode(
                        errors="replace"
                    ).strip()
                    or "Could not decode NEF file."
                )
            )

        if not ppm_file.exists() or ppm_file.stat().st_size == 0:
            raise HTTPException(
                status_code=422,
                detail="Could not decode the NEF image."
            )

        # -------------------------------------------------
        # PPM -> PDF
        # -------------------------------------------------

        run_command(
            [
                "magick",
                str(ppm_file),
                "-strip",
                str(output_file),
            ]
        )

        if not output_file.exists():
            raise HTTPException(
                status_code=422,
                detail="Could not create PDF from NEF image."
            )

        download_name = safe_name(filename, ".pdf")

        return FileResponse(
            str(output_file),
            media_type="application/pdf",
            filename=download_name,
            background=BackgroundTask(
                cleanup,
                str(workdir)
            ),
        )

    except HTTPException:
        cleanup(str(workdir))
        raise

    except Exception:
        cleanup(str(workdir))

        raise HTTPException(
            status_code=500,
            detail="NEF conversion failed."
        )

    finally:
        await file.close()


# =========================================================
# 3. ACSM
# =========================================================

@app.post("/convert-acsm")
async def convert_acsm(file: UploadFile = File(...)):

    """
    ACSM is not an ebook/PDF file.

    It is an Adobe Content Server Message used to obtain
    DRM-protected ebook content through Adobe's fulfillment
    system.

    Therefore this API deliberately does NOT pretend that
    ACSM can be converted using LibreOffice/ImageMagick.
    """

    filename = file.filename or ""

    if not filename.lower().endswith(".acsm"):
        raise HTTPException(
            status_code=400,
            detail="Only .acsm files are allowed."
        )

    raise HTTPException(
        status_code=501,
        detail=(
            "ACSM is an Adobe DRM fulfillment file, not a "
            "direct PDF conversion format. A licensed Adobe "
            "ebook fulfillment workflow is required."
        )
    )


# =========================================================
# Health
# =========================================================

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "converters": [
            "PUB -> PDF",
            "NEF -> PDF"
        ],
        "acsm": "requires Adobe DRM fulfillment workflow"
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "1024"))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port
    )