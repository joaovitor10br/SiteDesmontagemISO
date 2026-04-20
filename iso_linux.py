import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

def copy_with_progress(src, dst, progress_callback):
    # conta todos os arquivos primeiro
    total_files = 0
    for root, dirs, files in os.walk(src):
        total_files += len(files)

    copied_files = 0

    for root, dirs, files in os.walk(src):
        # recria estrutura de pastas
        rel_path = os.path.relpath(root, src)
        target_dir = os.path.join(dst, rel_path)
        os.makedirs(target_dir, exist_ok=True)

        for file in files:
            src_file = os.path.join(root, file)
            dst_file = os.path.join(target_dir, file)

            shutil.copy2(src_file, dst_file)

            copied_files += 1

            # calcula progresso entre 10% e 90%
            if progress_callback and total_files > 0:
                percent = 10 + int((copied_files / total_files) * 80)
                progress_callback(percent, f"Copiando arquivos ({copied_files}/{total_files})")


def run(cmd, check=True):
    print("+", " ".join(cmd))
    return subprocess.run(cmd, check=check)

def has_executable(name):
    from shutil import which
    return which(name) is not None

def extract_with_7z(iso_path, out_dir):
    if not has_executable("7z"):
        raise RuntimeError("7z não encontrado no PATH.")
    os.makedirs(out_dir, exist_ok=True)
    run(["7z", "x", "-y", "-o" + out_dir, iso_path])

def mount_loop_linux_and_copy(iso_path, out_dir, progress_callback=None):
    if platform.system() != "Linux":
        raise RuntimeError("Somente Linux.")

    progress_callback( 15, "Criando ponto de montagem...")
    mount_dir = tempfile.mkdtemp(prefix="iso-mount-")

    try:
        progress_callback( 25, "Montando ISO...")

        try:
            run(["mount", "-o", "loop,ro", iso_path, mount_dir])
        except subprocess.CalledProcessError:
            run(["sudo", "mount", "-o", "loop,ro", iso_path, mount_dir])

        progress_callback( 55, "Copiando arquivos da ISO...")
        copy_with_progress(mount_dir, out_dir, progress_callback)

    finally:
        progress_callback( 75, "Desmontando ISO...")
        try:
            run(["umount", mount_dir])
        except subprocess.CalledProcessError:
            run(["sudo", "umount", mount_dir])

        os.rmdir(mount_dir)

# 👉 FUNÇÃO PRINCIPAL REUTILIZÁVEL
def build_iso(iso_path, out_dir, progress_callback=None, deb_path=None):
    iso_path = os.path.abspath(iso_path)
    os.makedirs(out_dir, exist_ok=True)

    progress_callback( 5, "Preparando extração...")

    # Extrai a ISO usando mount ou 7z
    if platform.system() == "Linux":
        try:
            mount_loop_linux_and_copy(iso_path, out_dir, progress_callback)
            print("ISO montada com sucesso.")
        except Exception as e:
            print("Erro ao montar a ISO:", e)
            progress_callback(0, "Erro ao montar ISO")
            return {"method": "loop", "status": "fail"}
    else:
        progress_callback( 30, "Extraindo com 7z...")
        extract_with_7z(iso_path, out_dir)
        print("ISO extraída com 7z.")

    # Injeção de pacote
    if deb_path:
        progress_callback( 85, "Injetando pacotes...")
        inject_package(out_dir, deb_path)

    progress_callback( 100, "Finalizado!")

    return {"method": "iso", "status": "ok"}


def inject_package(iso_root, deb_path):
    # Aqui você cria o diretório extra_packages dentro da ISO desmontada
    target_dir = os.path.join(iso_root, "extra_packages")
    os.makedirs(target_dir, exist_ok=True)

    # Copia o pacote .deb para dentro da ISO
    shutil.copy2(deb_path, target_dir)
