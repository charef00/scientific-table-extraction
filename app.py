from flask import Flask, render_template, request, jsonify, session, redirect, url_for,send_from_directory, abort
from scopus import *
from pdf import *
import secrets
import time
app = Flask(__name__)
# Generate a secure random secret key
app.secret_key = secrets.token_hex(16)  # This generates a random 32-character hexadecimal string

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/search", methods=["POST"])
def search():
    keywords = request.form.get("keywords", "").strip()
    year_from = request.form.get("year_from")
    year_to = request.form.get("year_to")

    if not keywords:
        return render_template(
            "index.html",
            error="Please enter keywords"
        )

    # Convert years safely
    try:
        year_from = int(year_from) if year_from else None
    except ValueError:
        year_from = None

    try:
        year_to = int(year_to) if year_to else None
    except ValueError:
        year_to = None

    return render_template(
        "results.html",
        query=keywords,
        year_from=year_from,
        year_to=year_to
    )

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TABLES_DIR = os.path.join(BASE_DIR, "tables")

@app.route("/tables/<path:filename>")
def tables(filename):
    file_path = os.path.join(TABLES_DIR, filename)

    if not os.path.exists(file_path):
        abort(404)

    return send_from_directory(TABLES_DIR, filename)

@app.route("/load_more", methods=["POST"])
def load_more():
    data = request.get_json(force=True)

    # ✅ Required fields coming from JS
    required_fields = ["keywords", "start"]

    for field in required_fields:
        if field not in data:
            return jsonify({"error": f"Missing field: {field}"}), 400

    try:
        keywords = data["keywords"].strip()
        start = int(data["start"])

        year_from = data.get("year_from")
        year_to = data.get("year_to")
        field_type = int(data.get("field_type", 0))
        count = 20  # must match frontend

        if start < 0:
            return jsonify({"error": "Start must be non-negative"}), 400

        if not keywords:
            return jsonify({"error": "Keywords cannot be empty"}), 400

        if year_from:
            year_from = int(year_from)
        if year_to:
            year_to = int(year_to)

    except (ValueError, TypeError):
        return jsonify({"error": "Invalid data format"}), 400

    # 🔹 Multiple keywords support (semicolon-separated)
    keywords_list = [k.strip() for k in keywords.split(";") if k.strip()]
    if not keywords_list:
        return jsonify({"error": "No valid keywords found"}), 400

    query = " OR ".join(keywords_list)

    try:
        results = search_scopus(
            start=start,
            query=query,
            field_type=field_type,
            count=count,
            year_from=year_from,
            year_to=year_to
        )
        return jsonify(results)

    except Exception as e:
        app.logger.error(f"Scopus search error: {str(e)}")
        return jsonify({"error": "Search service unavailable"}), 500


@app.route("/start_processing", methods=["POST"])
def start_processing():
    # Get selected DOIs from the form
    selected_dois = request.form.getlist("selected_dois")
    
    if not selected_dois:
        return render_template("results.html", error="Please select at least one article to process")
    
    # Store DOIs in session
    session['dois'] = selected_dois
    session['total_dois'] = len(selected_dois)
    session['current_index'] = 0
    
    return redirect(url_for('processing'))

@app.route("/processing")
def processing():
    # Check if there are DOIs to process
    if 'dois' not in session or not session['dois']:
        return redirect(url_for('index'))
    
    # Get current DOI and progress information
    dois = session['dois']
    total_dois = session['total_dois']
    current_index = session.get('current_index', 0)
    
    if current_index >= total_dois:
        # All DOIs processed
        images = os.listdir("tables")
        return render_template("tables_select.html", total=total_dois,images=images)
    
    current_doi = dois[current_index]
    progress = current_index + 1
    #-------------------------------------- traitement is here -----------------------------------
    #print(download_pdf_from_scihub(current_doi))
    table=get_paper_by_doi(current_doi)

    #
    # 🔹 Safety check
    if not table:
        app.logger.warning(f"No data found for DOI: {current_doi}")

        return render_template(
            "processing.html",
            current_doi=current_doi,
            progress=progress,
            total=total_dois,
            year="N/A",
            current_index=current_index,
            title="Metadata not available"
        )
    
    # 🔹 Safe access
    publisher = table.get("publisher", "Unknown")
    if "springer" in publisher.lower():
        publisher="springer"
    if "tandf" in publisher.lower():
        publisher="tandfonline"
    year = table.get("year", 2026)
    title = table.get("title", "Untitled")
    download_pdf(current_doi, publisher, year)
    filename = current_doi.replace("/", "_") + ".pdf"
    file_path = os.path.join("papers", filename)
    if os.path.isfile(file_path):
        pdf_to_tables_png(pdf_path=file_path)
    #--------------------------------------------------------------------------------------------
    return render_template(
        "processing.html",
        current_doi=current_doi,
        progress=progress,
        total=total_dois,
        year=year,
        current_index=current_index,
        title=title
    )


@app.route("/process_next", methods=["POST"])
def process_next():
    # Check if there are DOIs to process
    if 'dois' not in session or not session['dois']:
        return jsonify({"error": "No DOIs to process"}), 400
    # Get current index and increment
    current_index = session.get('current_index', 0)
    current_index += 1
    session['current_index'] = current_index
    
    # Remove processed DOI from the list
    if current_index < len(session['dois']):
        # Still have DOIs to process
        return jsonify({
            "next": True,
            "current_doi": session['dois'][current_index],
            "progress": current_index + 1,
            "total": session['total_dois']
        })
    else:
        # All DOIs processed
        return jsonify({
            "next": False,
            "complete": True
        })
    
@app.route("/process-tables", methods=["POST"])
def process_tables():
    selected_tables = request.form.getlist("selected_images")

    session["tables_queue"] = selected_tables
    session["tables_total"] = len(selected_tables)

    return redirect(url_for("process_next_table"))


@app.route("/process-next-table")
def process_next_table():
    tables_queue = session.get("tables_queue", [])
    total = session.get("tables_total", 0)

    if not tables_queue:
        return render_template("done.html")

    current_image = tables_queue[0]  # ⚠️ do NOT pop yet

    processed = total - len(tables_queue)
    progress = int((processed / total) * 100) if total else 100

    return render_template(
        "progress.html",
        current_image=current_image,
        progress=progress,
        processed=processed,
        total=total
    )


@app.route("/run-ocr", methods=["GET"])
def run_ocr():
    tables_queue = session.get("tables_queue", [])

    if not tables_queue:
        return redirect(url_for("process_next_table"))

    time.sleep(3)
    current_image = tables_queue.pop(0)
    # ---------------- SLOW OCR ------------------
    table_html=extract_table_html(f"tables/{current_image}")
    new_name = current_image.rsplit(".", 1)[0] + ".xlsx"
    html_table_to_excel(html_table=table_html, output_path= f"excel/{new_name}")
    session["tables_queue"] = tables_queue

    return redirect(url_for("process_next_table"))


@app.route("/clear-all", methods=["POST"])
def clear_all():
    folders = ["papers", "tables", "excel"]

    for folder in folders:
        if os.path.exists(folder):
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)

                if os.path.isfile(file_path):
                    os.remove(file_path)

    return jsonify({"status": "success"})
if __name__ == "__main__":
    app.run(debug=True)