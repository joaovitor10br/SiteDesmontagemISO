@app.route("/build/linux", methods=["POST"])
def build_linux():
    iso = request.files.get("iso")
    files = request.files.getlist("out_dir")

    if not iso or not files:
        return jsonify({"error": "Dados inválidos"}), 400

    # salva ISO
    iso_path = os.path.join("/tmp/uploads", iso.filename)
    iso.save(iso_path)

    # descobre nome da pasta base
    first_file = files[0]
    base_folder = first_file.filename.split("/")[0]

    out_dir = os.path.join("/home/joao/Iso_extraida", base_folder)
    os.makedirs(out_dir, exist_ok=True)

    build_iso(iso_path, out_dir)

    return jsonify({
        "status": "Build iniciado",
        "output": out_dir
    })
