import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

def find_isohdpfx():
    possible_paths = [
        "/usr/lib/ISOLINUX/isohdpfx.bin",
        "/usr/lib/syslinux/isohdpfx.bin",
        "/usr/lib/syslinux/bios/isohdpfx.bin",
    ]

    for path in possible_paths:
        if os.path.exists(path):
            return path

    raise RuntimeError("isohdpfx.bin não encontrado. Instale syslinux.")



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


def run(cmd, check=True, allow_codes=(0,)):
    print("+", " ".join(cmd))
    result = subprocess.run(cmd)

    if check and result.returncode not in allow_codes:
        raise subprocess.CalledProcessError(result.returncode, cmd)

    return result

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

        progress_callback(55, "Copiando arquivos da ISO com rsync...")

        run([
            "sudo", "rsync",
            "-aH",
            "--delete",
            f"{mount_dir}/",
            f"{out_dir}/"
        ], allow_codes=(0, 1))

    finally:
        progress_callback( 75, "Desmontando ISO...")
        try:
            run(["umount", mount_dir])
        except subprocess.CalledProcessError:
            run(["sudo", "umount", mount_dir])

        os.rmdir(mount_dir)

# 👉 FUNÇÃO PRINCIPAL REUTILIZÁVEL
def build_iso(iso_path, out_dir, progress_callback=None, deb_paths=None):
    iso_path = os.path.abspath(iso_path)
    os.makedirs(out_dir, exist_ok=True)

    progress_callback( 5, "Preparando extração...")

    # Extrai a ISO usando mount ou 7z
    # sempre extrair com 7z (metodo confiável)
    extract_with_7z(iso_path, out_dir)
    print("ISO extraída com 7z.")

    # Injeção de pacote
    # Injeção de pacotes .deb
    # Injeção real no sistema live
    if deb_paths:
        customize_live_system(out_dir, deb_paths, progress_callback)

    create_bootable_iso(out_dir, out_dir, progress_callback)

    progress_callback(100, "Finalizado!")

    return {"method": "iso", "status": "ok"}

def customize_live_system(iso_root, deb_paths, progress_callback):
    import subprocess

    squashfs_path = os.path.join(iso_root, "casper/filesystem.squashfs")

    if not os.path.exists(squashfs_path):
        progress_callback(0, "ISO não é Ubuntu/Debian live 😢")
        return

    work_dir = os.path.join(iso_root, "squashfs-root")

    progress_callback(88, "Extraindo sistema interno (squashfs)...")

    # garante DNS dentro do chroot (internet)
    shutil.copy("/etc/resolv.conf", work_dir + "/etc/resolv.conf")

    run(["unsquashfs", "-d", work_dir, squashfs_path])

    # monta pseudo-filesystems para chroot funcionar
    progress_callback(90, "Preparando ambiente chroot...")
    run(["sudo", "umount", "-lf", work_dir + "/dev"])
    run(["sudo", "umount", "-lf", work_dir + "/proc"])
    run(["sudo", "umount", "-lf", work_dir + "/sys"])

    try:
        # copia os .deb pra dentro do sistema live
        deb_target = os.path.join(work_dir, "tmp/debs")
        os.makedirs(deb_target, exist_ok=True)

        for deb in deb_paths:
            shutil.copy2(deb, deb_target)

        progress_callback(92, "Instalando pacotes dentro da ISO...")

        # instala os pacotes via chroot 😎
        run([
            "sudo", "chroot", work_dir,
            "bash", "-c",
            "dpkg -i /tmp/debs/*.deb || apt-get -f install -y"
        ])

    finally:
        progress_callback(95, "Desmontando ambiente chroot...")
        run(["sudo", "umount", work_dir + "/dev"])
        run(["sudo", "umount", work_dir + "/proc"])
        run(["sudo", "umount", work_dir + "/sys"])

    # recria o squashfs
    progress_callback(97, "Recompactando sistema...")
    run([
        "sudo", "mksquashfs",
        work_dir,
        squashfs_path,
        "-noappend"
    ])
    shutil.rmtree(work_dir)


def detect_boot_config(iso_root):
    """
    Detecta o sistema de boot presente na ISO extraída.

    Retorna um dicionário com os caminhos necessários
    para reconstruir a ISO usando xorriso.
    """

    # ==========================================================
    # Arch / ArchISO
    # ==========================================================

    arch_boot_image = os.path.join(
        iso_root,
        "boot/syslinux/isolinux.bin"
    )

    if os.path.exists(arch_boot_image):
        boot_catalog = os.path.join(
            iso_root,
            "boot/syslinux/boot.cat"
        )

        efi_image = os.path.join(
            iso_root,
            "EFI/BOOT/BOOTx64.EFI"
        )

        if not os.path.exists(boot_catalog):
            raise RuntimeError(
                f"Catálogo de boot não encontrado: {boot_catalog}"
            )

        if not os.path.exists(efi_image):
            raise RuntimeError(
                f"Imagem EFI não encontrada: {efi_image}"
            )

        print("DEBUG: Estrutura de boot Arch/ArchISO detectada.")

        return {
            "type": "arch",
            "boot_catalog": "boot/syslinux/boot.cat",
            "boot_image": "boot/syslinux/isolinux.bin",
            "efi_image": "EFI/BOOT/BOOTx64.EFI",
        }

    # ==========================================================
    # Debian / Ubuntu / GRUB
    # ==========================================================

    grub_boot_image = os.path.join(
        iso_root,
        "boot/grub/i386-pc/eltorito.img"
    )

    if os.path.exists(grub_boot_image):
        efi_image = os.path.join(
            iso_root,
            "EFI/boot/bootx64.efi"
        )

        # Algumas ISOs utilizam letras maiúsculas.
        if not os.path.exists(efi_image):
            efi_image = os.path.join(
                iso_root,
                "EFI/BOOT/BOOTx64.EFI"
            )

        if not os.path.exists(efi_image):
            raise RuntimeError(
                "Imagem EFI do GRUB não encontrada."
            )

        print("DEBUG: Estrutura de boot GRUB/Debian detectada.")

        return {
            "type": "grub",
            "boot_catalog": "boot.catalog",
            "boot_image": "boot/grub/i386-pc/eltorito.img",
            "efi_image": os.path.relpath(
                efi_image,
                iso_root
            ),
        }

    # ==========================================================
    # Nenhuma estrutura conhecida
    # ==========================================================

    raise RuntimeError(
        "Estrutura de boot não reconhecida. "
        "Não foi encontrado um bootloader compatível."
    )


def create_bootable_iso(iso_root, output_dir, progress_callback):
    """
    Cria a ISO final a partir do diretório extraído.
    """

    progress_callback(98, "Gerando ISO final...")

    # ----------------------------------------------------------
    # Caminho da ISO final
    # ----------------------------------------------------------

    output_dir = os.path.abspath(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    final_iso = os.path.join(
        output_dir,
        "custom_linux.iso"
    )

    # ----------------------------------------------------------
    # Localiza o isohdpfx.bin
    # ----------------------------------------------------------

    isohdpfx = find_isohdpfx()

    print(f"DEBUG isohdpfx: {isohdpfx}")
    print(f"DEBUG existe: {os.path.exists(isohdpfx)}")

    if not os.path.exists(isohdpfx):
        raise RuntimeError(
            f"isohdpfx.bin não encontrado: {isohdpfx}"
        )

    # ----------------------------------------------------------
    # Detecta o bootloader
    # ----------------------------------------------------------

    boot_config = detect_boot_config(iso_root)

    print(
        f"DEBUG tipo de boot: {boot_config['type']}"
    )

    print(
        f"DEBUG boot catalog: "
        f"{boot_config['boot_catalog']}"
    )

    print(
        f"DEBUG boot image: "
        f"{boot_config['boot_image']}"
    )

    print(
        f"DEBUG EFI image: "
        f"{boot_config['efi_image']}"
    )

    # ----------------------------------------------------------
    # Remove ISO anterior, caso exista
    # ----------------------------------------------------------

    if os.path.exists(final_iso):
        print(
            f"DEBUG removendo ISO anterior: {final_iso}"
        )

        os.remove(final_iso)

    # ----------------------------------------------------------
    # Monta comando xorriso
    # ----------------------------------------------------------

    command = [
        "xorriso",
        "-as", "mkisofs",

        # ISO filesystem
        "-r",
        "-V", "CustomLinux",
        "-J",
        "-l",
        "-iso-level", "3",

        # MBR híbrido
        "-isohybrid-mbr", isohdpfx,

        # BIOS / El Torito
        "-c", boot_config["boot_catalog"],
        "-b", boot_config["boot_image"],
        "-no-emul-boot",
        "-boot-load-size", "4",
        "-boot-info-table",

        # UEFI
        "-eltorito-alt-boot",
        "-e", boot_config["efi_image"],
        "-no-emul-boot",

        # GPT híbrido
        "-isohybrid-gpt-basdat",

        # Saída
        "-o", final_iso,

        # Conteúdo
        iso_root,
    ]

    print()
    print("DEBUG comando xorriso:")

    print(
        " ".join(
            f'"{arg}"' if " " in arg else arg
            for arg in command
        )
    )

    print()

    # ----------------------------------------------------------
    # Executa xorriso
    # ----------------------------------------------------------

    run(command)

    # ----------------------------------------------------------
    # Verifica se a ISO realmente foi criada
    # ----------------------------------------------------------

    if not os.path.exists(final_iso):
        raise RuntimeError(
            "O xorriso terminou, mas a ISO final "
            "não foi encontrada."
        )

    iso_size = os.path.getsize(final_iso)

    if iso_size == 0:
        raise RuntimeError(
            "A ISO final foi criada, mas está vazia."
        )

    progress_callback(
        100,
        "ISO criada com sucesso!"
    )

    print(
        f"ISO criada com sucesso: {final_iso}"
    )

    print(
        f"DEBUG tamanho da ISO: "
        f"{iso_size / (1024 ** 2):.2f} MB"
    )

    return final_iso