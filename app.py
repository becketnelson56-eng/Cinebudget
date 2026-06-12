import os
import pathlib
import shutil
import sys
import tempfile
import threading
import uuid
from functools import wraps

import anthropic
from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from api_client import get_breakdown
from breakdown import _deduplicate_accounts, _enforce_cross_reference_flags, _enforce_flow_through, _suppress_misrouted_rows
from excel_writer import write_breakdown

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24))

USERNAME = os.environ.get('APP_USERNAME', 'admin')
_PASSWORD_HASH = generate_password_hash(os.environ.get('APP_PASSWORD', 'breakdown2024'))

# MERGE ARCHITECTURE — enforced when backend is built:
# When a user uploads an existing .xlsx alongside a new PDF, the tool must NEVER
# delete, overwrite, or reorder existing rows. It only adds new information into
# blank Rate/Amount cells where a match is found, or appends new rows at the bottom
# of the relevant account tab. Output must be saved as [original-name]-updated.xlsx
# and must never overwrite the original file.

# In-memory job store: job_id -> state dict
# Keys: status ("running"|"done"|"error"), step, download_name, result_path, error
_jobs: dict = {}
_jobs_lock = threading.Lock()


# ── Auth decorator ────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ── Pipeline runner ───────────────────────────────────────────────────────────

def _run_pipeline(job_id: str, pdf_path: str, pdf_bytes: bytes, tmp_dir: str) -> None:
    tag = f"[job {job_id[:8]}]"

    def set_step(msg: str) -> None:
        print(f"{tag} {msg}", flush=True)
        with _jobs_lock:
            _jobs[job_id]["step"] = msg

    def fail(msg: str) -> None:
        print(f"{tag} ERROR: {msg}", file=sys.stderr, flush=True)
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = msg
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"{tag} Starting pipeline — {os.path.basename(pdf_path)}", flush=True)

    # Step 1 — API extraction (the slow part, ~1–2 min)
    try:
        set_step("Sending screenplay to Claude API…")
        data = get_breakdown(pdf_bytes)
    except anthropic.AuthenticationError:
        return fail("API authentication failed — check that ANTHROPIC_API_KEY is set correctly.")
    except anthropic.RateLimitError:
        return fail("API rate limit reached. Please wait a minute and try again.")
    except anthropic.APIConnectionError:
        return fail("Could not reach the Claude API. Check your internet connection.")
    except anthropic.APIError as exc:
        return fail(f"Claude API error: {exc}")
    except Exception as exc:
        return fail(f"Extraction failed: {exc}")

    scene_count = len(data.get("scene_breakdown", []))
    total_items = sum(len(v) for v in data.get("accounts", {}).values())
    print(f"{tag} Extraction complete: {scene_count} scene(s), {total_items} line item(s)", flush=True)

    if scene_count == 0 and total_items == 0:
        return fail("No screenplay content was detected in this file. Please upload a properly formatted screenplay PDF.")

    # Steps 2–3 — post-processing and write
    try:
        set_step("Running structural consistency checks…")
        _suppress_misrouted_rows(data)
        _enforce_cross_reference_flags(data)
        _enforce_flow_through(data)
        _deduplicate_accounts(data)

        set_step("Writing breakdown spreadsheet…")
        xlsx_path = os.path.join(tmp_dir, "breakdown.xlsx")
        write_breakdown(data, xlsx_path, pdf_path)

        print(f"{tag} Done — {scene_count} scenes, {total_items} items → {os.path.basename(xlsx_path)}", flush=True)
        with _jobs_lock:
            _jobs[job_id].update({
                "status": "done",
                "step": "Complete",
                "result_path": xlsx_path,
            })
    except Exception as exc:
        fail(f"Failed to write spreadsheet: {exc}")


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    if "user" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user" in session:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == USERNAME and check_password_hash(_PASSWORD_HASH, password):
            session["user"] = username
            return redirect(url_for("dashboard"))
        error = "Invalid credentials."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    tab = request.args.get("tab", "budget")
    valid_tabs = {"budget", "breakdown", "scheduling", "deals", "export"}
    if tab not in valid_tabs:
        tab = "budget"
    return render_template("dashboard.html", active_tab=tab)


# ── Breakdown API routes ──────────────────────────────────────────────────────

@app.route("/run-breakdown", methods=["POST"])
def run_breakdown_route():
    if "user" not in session:
        return jsonify({"error": "Not authenticated."}), 401

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY is not configured on this server."}), 400

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file uploaded."}), 400

    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Uploaded file must be a PDF."}), 400

    pdf_bytes = file.read()
    if len(pdf_bytes) == 0:
        return jsonify({"error": "Uploaded PDF is empty."}), 400

    # Derive download filename: my-script.pdf → my-script-breakdown.xlsx
    stem = pathlib.Path(file.filename).stem
    for suffix in ("-script", "_script", "-screenplay", "_screenplay"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    download_name = f"{stem}-breakdown.xlsx"

    # Temp directory scoped to this job
    tmp_dir = tempfile.mkdtemp(prefix="cinebudget_")
    pdf_path = os.path.join(tmp_dir, file.filename)
    with open(pdf_path, "wb") as fh:
        fh.write(pdf_bytes)

    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running",
            "step": "Starting…",
            "download_name": download_name,
            "result_path": None,
            "error": None,
        }

    threading.Thread(
        target=_run_pipeline,
        args=(job_id, pdf_path, pdf_bytes, tmp_dir),
        daemon=True,
    ).start()

    return jsonify({"job_id": job_id})


@app.route("/job-status/<job_id>")
def job_status(job_id: str):
    if "user" not in session:
        return jsonify({"error": "Not authenticated."}), 401
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found."}), 404
    return jsonify({
        "status": job["status"],
        "step": job.get("step", ""),
        "error": job.get("error"),
    })


@app.route("/download/<job_id>")
def download_result(job_id: str):
    if "user" not in session:
        return redirect(url_for("login"))
    with _jobs_lock:
        job = _jobs.get(job_id)
    if not job or job["status"] != "done":
        return "File not ready or job not found.", 404

    result_path = job["result_path"]
    download_name = job["download_name"]
    tmp_dir = os.path.dirname(result_path)

    # Read into memory so temp files can be deleted immediately
    with open(result_path, "rb") as fh:
        xlsx_data = fh.read()

    shutil.rmtree(tmp_dir, ignore_errors=True)
    with _jobs_lock:
        _jobs.pop(job_id, None)

    return Response(
        xlsx_data,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )


@app.route("/deals", methods=["POST"])
def deals_route():
    if "user" not in session:
        return jsonify({"error": "Not authenticated."}), 401
    file = request.files.get("contract")
    if not file or not file.filename:
        return jsonify({"error": "Please upload a contract PDF before processing."}), 400
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are accepted. Please upload a .pdf file."}), 400
    return jsonify({"status": "coming_soon", "message": "Deal processing pipeline coming soon."})


@app.route("/scheduling", methods=["POST"])
def scheduling_route():
    if "user" not in session:
        return jsonify({"error": "Not authenticated."}), 401
    file = request.files.get("schedule")
    if not file or not file.filename:
        return jsonify({"error": "Please upload a schedule document before processing."}), 400
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Only PDF files are accepted. Please upload a .pdf file."}), 400
    return jsonify({"status": "coming_soon", "message": "Scheduling pipeline coming soon."})


# ── Global error handlers ─────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Page not found."), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500, message="An unexpected error occurred. Please try again."), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
