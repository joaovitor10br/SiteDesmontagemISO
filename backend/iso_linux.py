import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

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

def mount_loop_linux_and_copy(iso_path, out_dir):
    if platform.system() != "Linux":
        raise RuntimeError("Somente Linux.")
    mount_dir = tempfile.mkdtemp(prefix="iso-mount-")
    try:
        try:
            run(["mount", "-o", "loop,ro", iso_path, mount_dir])
        except subprocess.CalledProcessError:
            run(["sudo", "mount", "-o", "loop,ro", iso_path, mount_dir])

        shutil.copytree(mount_dir, out_dir, dirs_exist_ok=True)
    finally:
        try:
            run(["umount", mount_dir])
        except subprocess.CalledProcessError:
            run(["sudo", "umount", mount_dir])
        os.rmdir(mount_dir)

# 👉 FUNÇÃO PRINCIPAL REUTILIZÁVEL
def build_iso(iso_path, out_dir):
    iso_path = os.path.abspath(iso_path)
    os.makedirs(out_dir, exist_ok=True)

    if platform.system() == "Linux":
        try:
            mount_loop_linux_and_copy(iso_path, out_dir)
            return {"method": "loop", "status": "ok"}
        except Exception as e:
            print("Loop falhou:", e)

    extract_with_7z(iso_path, out_dir)
    return {"method": "7z", "status": "ok"}
