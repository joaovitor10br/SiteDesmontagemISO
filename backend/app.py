from flask import Flask, request, jsonify
import os
from iso_linux import build_iso

app = Flask(__name__)

BASE_DIR = "/home/joao/iso_builder"
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
EXTRACT_DIR = os.path.join(BASE_DIR, "extracted")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(EXTRACT_DIR, exist_ok=True)


@app.route("/build/linux", methods=["POST"])
def build_linux():
    iso = request.files.get("iso")

    if not iso:
        return jsonify({"error": "ISO não enviada"}), 400

    iso_path = os.path.join(UPLOAD_DIR, iso.filename)
    iso.save(iso_path)

    output_dir = os.path.join(EXTRACT_DIR, "mx_test")
    os.makedirs(output_dir, exist_ok=True)

    result = build_iso(iso_path, output_dir)

    return jsonify({
        "status": "ISO extraída",
        "method": result["method"],
        "output": output_dir
    })


if __name__ == "__main__":
    app.run(debug=True)
