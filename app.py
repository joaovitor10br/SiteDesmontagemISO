from flask import Flask, request, jsonify, render_template # Adicionei render_template
import os
import threading
import uuid
from iso_linux import build_iso

tasks = {}

def validar_diretorio_usuario(path):
    if not path:
        return None

    # permite usar "~" como home do usuário
    path = os.path.expanduser(path)

    # transforma em caminho absoluto
    path = os.path.abspath(path)

    # ❌ impedir diretórios perigosos
    diretorios_bloqueados = [
        "/", "/root", "/etc", "/bin", "/usr", "/boot", "/dev", "/proc", "/sys"
    ]

    if path in diretorios_bloqueados:
        return None

    try:
        # cria a pasta caso não exista
        os.makedirs(path, exist_ok=True)
    except Exception:
        return None

    return path

# IMPORTANTE: Avisamos ao Flask que os arquivos estão na pasta ao lado
app = Flask(__name__, 
            template_folder='../frontend', 
            static_folder='../frontend',
            static_url_path='') 

BASE_DIR = "/home/joao/iso_builder"
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
EXTRACT_DIR = os.path.join(BASE_DIR, "extracted")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(EXTRACT_DIR, exist_ok=True)

# --- NOVA ROTA PARA O FRONT-END ---
@app.route("/")
def index():
    # Isso vai procurar o index.html dentro da pasta /frontend
    return render_template("index.html")

# --- ROTA PARA O SOBRE ---
@app.route("/sobre.html")
def sobre():
    return render_template("sobre.html")
# ----------------------------------

@app.route("/progress/<task_id>")
def progress(task_id):
    task = tasks.get(task_id)

    if not task:
        return jsonify({"error": "Tarefa não encontrada"}), 404

    return jsonify(task)

@app.route("/build/linux", methods=["POST"])
def build_linux():
    iso = request.files.get("iso")
    output_dir = request.form.get("output_dir")

    if not iso:
        return jsonify({"error": "ISO não enviada"}), 400

    output_dir = validar_diretorio_usuario(output_dir)
    if not output_dir:
        return jsonify({"error": "Diretório inválido"}), 400

    # salva ISO
    iso_path = os.path.join(UPLOAD_DIR, iso.filename)
    iso.save(iso_path)

    # cria ID da tarefa
    task_id = str(uuid.uuid4())

    # cria progresso inicial
    tasks[task_id] = {
        "progress": 0,
        "status": "Iniciando..."
    }

    # função que roda em background
    def run_build():
        try:
            def progress_callback(value, status):
                tasks[task_id]["progress"] = value
                tasks[task_id]["status"] = status

            build_iso(iso_path, output_dir, progress_callback)

            tasks[task_id]["output"] = output_dir

        except Exception as e:
            tasks[task_id]["status"] = f"Erro: {str(e)}"

    thread = threading.Thread(target=run_build)
    thread.start()

    # retorna id da tarefa
    return jsonify({"task_id": task_id})

if __name__ == "__main__":
    app.run(debug=True)
