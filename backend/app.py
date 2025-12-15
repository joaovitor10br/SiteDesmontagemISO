from flask import Flask, request, jsonify
from iso_linux import build_iso

app = Flask(__name__)

@app.route("/build/linux", methods=["POST"])
def build_linux():
    iso = request.form.get("iso")
    out_dir = request.form.get("out_dir")

    if not iso or not out_dir:
        return jsonify({"error": "Parâmetros faltando"}), 400

    result = build_iso(iso, out_dir)

    return jsonify({
        "status": "Build iniciado",
        "details": result
    })

if __name__ == "__main__":
    app.run(debug=True)
