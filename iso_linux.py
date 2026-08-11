import os
import platform
import shutil
import subprocess
import tempfile


# ==========================================================
# Utilidades
# ==========================================================

def report_progress(progress_callback, percent, message):
    """
    Envia atualização de progresso caso exista callback.
    """

    if progress_callback:
        progress_callback(percent, message)


def run(cmd, check=True, allow_codes=(0,)):
    """
    Executa um comando externo.

    Args:
        cmd: lista contendo comando e argumentos.
        check: se True, gera exceção para códigos não permitidos.
        allow_codes: códigos de retorno considerados válidos.

    Returns:
        subprocess.CompletedProcess
    """

    cmd = [str(arg) for arg in cmd]

    print("+", " ".join(cmd))

    result = subprocess.run(cmd)

    if check and result.returncode not in allow_codes:
        raise subprocess.CalledProcessError(
            result.returncode,
            cmd
        )

    return result


def has_executable(name):
    """
    Verifica se um executável existe no PATH.
    """

    return shutil.which(name) is not None


def remove_path(path):
    """
    Remove arquivo, link ou diretório.

    Os arquivos relacionados ao processo de criação da ISO
    frequentemente pertencem ao root por causa de unsquashfs,
    chroot e mksquashfs. Por isso a remoção usa sudo.
    """

    if not os.path.lexists(path):
        return

    if os.path.isdir(path) and not os.path.islink(path):
        run([
            "sudo",
            "rm",
            "-rf",
            path
        ])
    else:
        run([
            "sudo",
            "rm",
            "-f",
            path
        ])


def ensure_directory(path):
    """
    Garante que um diretório exista.
    """

    os.makedirs(
        path,
        exist_ok=True
    )


# ==========================================================
# Dependências
# ==========================================================

def validate_dependencies():
    """
    Verifica as ferramentas necessárias para o processo.
    """

    required = [
        "7z",
        "unsquashfs",
        "mksquashfs",
        "xorriso",
    ]

    missing = [
        executable
        for executable in required
        if not has_executable(executable)
    ]

    if missing:
        raise RuntimeError(
            "Dependências não encontradas: "
            + ", ".join(missing)
        )


# ==========================================================
# ISO / Boot
# ==========================================================

def find_isohdpfx():
    """
    Procura pelo isohdpfx.bin usado na criação da ISO híbrida.
    """

    possible_paths = [
        "/usr/lib/ISOLINUX/isohdpfx.bin",
        "/usr/lib/syslinux/isohdpfx.bin",
        "/usr/lib/syslinux/bios/isohdpfx.bin",
        "/usr/share/syslinux/isohdpfx.bin",
        "/usr/share/syslinux/isohdpfx.bin",
    ]

    for path in possible_paths:
        if os.path.isfile(path):
            return path

    raise RuntimeError(
        "isohdpfx.bin não encontrado. "
        "Instale o pacote syslinux."
    )


# ==========================================================
# Extração da ISO
# ==========================================================

def extract_with_7z(iso_path, out_dir):
    """
    Extrai o conteúdo da ISO usando 7z.
    """

    if not has_executable("7z"):
        raise RuntimeError(
            "7z não encontrado no PATH."
        )

    iso_path = os.path.abspath(iso_path)
    out_dir = os.path.abspath(out_dir)

    if not os.path.isfile(iso_path):
        raise RuntimeError(
            f"ISO não encontrada: {iso_path}"
        )

    ensure_directory(out_dir)

    run([
        "7z",
        "x",
        "-y",
        f"-o{out_dir}",
        iso_path
    ])


def mount_loop_linux_and_copy(
    iso_path,
    out_dir,
    progress_callback=None
):
    """
    Monta uma ISO somente leitura e copia seu conteúdo.

    Esta função é mantida como alternativa à extração por 7z.
    """

    if platform.system() != "Linux":
        raise RuntimeError(
            "A montagem de ISO está disponível somente no Linux."
        )

    iso_path = os.path.abspath(iso_path)
    out_dir = os.path.abspath(out_dir)

    mount_dir = tempfile.mkdtemp(
        prefix="iso-mount-"
    )

    mounted = False

    try:
        report_progress(
            progress_callback,
            15,
            "Montando ISO..."
        )

        try:
            run([
                "mount",
                "-o",
                "loop,ro",
                iso_path,
                mount_dir
            ])

        except subprocess.CalledProcessError:
            run([
                "sudo",
                "mount",
                "-o",
                "loop,ro",
                iso_path,
                mount_dir
            ])

        mounted = True

        ensure_directory(out_dir)

        report_progress(
            progress_callback,
            55,
            "Copiando arquivos da ISO..."
        )

        run([
            "sudo",
            "rsync",
            "-aH",
            "--delete",
            f"{mount_dir}/",
            f"{out_dir}/"
        ])

    finally:
        if mounted:
            report_progress(
                progress_callback,
                75,
                "Desmontando ISO..."
            )

            result = run([
                "umount",
                mount_dir
            ], check=False)

            if result.returncode != 0:
                run([
                    "sudo",
                    "umount",
                    mount_dir
                ], check=False)

        try:
            os.rmdir(mount_dir)
        except OSError:
            pass


# ==========================================================
# SquashFS
# ==========================================================

def detect_squashfs(iso_root):
    """
    Detecta o SquashFS presente na ISO.

    Suporta estruturas comuns de:

        Debian / Ubuntu:
            casper/filesystem.squashfs
            live/filesystem.squashfs

        Arch:
            arch/x86_64/airootfs.sfs
    """

    iso_root = os.path.abspath(iso_root)

    candidates = [
        os.path.join(
            iso_root,
            "casper",
            "filesystem.squashfs"
        ),
        os.path.join(
            iso_root,
            "live",
            "filesystem.squashfs"
        ),
        os.path.join(
            iso_root,
            "arch",
            "x86_64",
            "airootfs.sfs"
        ),
    ]

    for path in candidates:
        if os.path.isfile(path):
            path = os.path.abspath(path)

            print(
                f"DEBUG SquashFS encontrado: {path}"
            )

            return path

    raise RuntimeError(
        "Filesystem SquashFS não encontrado na ISO."
    )


def detect_distro(iso_root):
    """
    Detecta a família da distribuição com base
    na localização do SquashFS.
    """

    squashfs_path = detect_squashfs(
        iso_root
    )

    normalized = squashfs_path.replace(
        os.sep,
        "/"
    )

    if (
        "/casper/filesystem.squashfs" in normalized
        or "/live/filesystem.squashfs" in normalized
    ):
        print(
            "DEBUG distribuição detectada: Debian/Ubuntu"
        )

        return "debian"

    if normalized.endswith(
        "/arch/x86_64/airootfs.sfs"
    ):
        print(
            "DEBUG distribuição detectada: Arch"
        )

        return "arch"

    raise RuntimeError(
        "Não foi possível determinar a distribuição."
    )


# ==========================================================
# Chroot
# ==========================================================

def prepare_chroot(work_dir):
    """
    Cria os diretórios necessários para o ambiente chroot.
    """

    directories = [
        os.path.join(work_dir, "dev"),
        os.path.join(work_dir, "proc"),
        os.path.join(work_dir, "sys"),
        os.path.join(work_dir, "run"),
    ]

    for directory in directories:
        run([
            "sudo",
            "mkdir",
            "-p",
            directory
        ])


def configure_dns(work_dir):
    """
    Copia o resolv.conf do sistema hospedeiro
    para dentro do sistema Live.
    """

    resolv_conf = os.path.join(
        work_dir,
        "etc",
        "resolv.conf"
    )

    try:
        run([
            "sudo",
            "rm",
            "-f",
            resolv_conf
        ], check=False)

        run([
            "sudo",
            "cp",
            "/etc/resolv.conf",
            resolv_conf
        ])

        print(
            "DEBUG DNS configurado."
        )

    except Exception as exc:
        print(
            f"WARNING: não foi possível configurar DNS: {exc}"
        )


def mount_chroot_filesystems(work_dir):
    """
    Monta /dev, /proc, /sys e /run dentro do chroot.

    Returns:
        Lista contendo os pontos de montagem realizados.
    """

    dev_dir = os.path.join(
        work_dir,
        "dev"
    )

    proc_dir = os.path.join(
        work_dir,
        "proc"
    )

    sys_dir = os.path.join(
        work_dir,
        "sys"
    )

    run_dir = os.path.join(
        work_dir,
        "run"
    )

    mounted = []

    try:
        # --------------------------------------------------
        # /dev
        # --------------------------------------------------

        run([
            "sudo",
            "mount",
            "--rbind",
            "/dev",
            dev_dir
        ])

        run([
            "sudo",
            "mount",
            "--make-rslave",
            dev_dir
        ])

        mounted.append(
            ("recursive", dev_dir)
        )

        # --------------------------------------------------
        # /proc
        # --------------------------------------------------

        run([
            "sudo",
            "mount",
            "-t",
            "proc",
            "proc",
            proc_dir
        ])

        mounted.append(
            ("normal", proc_dir)
        )

        # --------------------------------------------------
        # /sys
        # --------------------------------------------------

        run([
            "sudo",
            "mount",
            "--rbind",
            "/sys",
            sys_dir
        ])

        run([
            "sudo",
            "mount",
            "--make-rslave",
            sys_dir
        ])

        mounted.append(
            ("recursive", sys_dir)
        )

        # --------------------------------------------------
        # /run
        # --------------------------------------------------

        run([
            "sudo",
            "mount",
            "--rbind",
            "/run",
            run_dir
        ])

        run([
            "sudo",
            "mount",
            "--make-rslave",
            run_dir
        ])

        mounted.append(
            ("recursive", run_dir)
        )

        return mounted

    except Exception:
        unmount_chroot_filesystems(
            mounted
        )
        raise


def unmount_chroot_filesystems(mounted):
    """
    Desmonta os pseudo-filesystems do chroot
    na ordem inversa da montagem.
    """

    for mount_type, mount_point in reversed(
        mounted
    ):
        if mount_type == "recursive":
            run([
                "sudo",
                "umount",
                "-R",
                mount_point
            ], check=False)

        else:
            run([
                "sudo",
                "umount",
                mount_point
            ], check=False)


def test_chroot(work_dir):
    """
    Testa se o sistema extraído consegue iniciar um chroot.
    """

    run([
        "sudo",
        "chroot",
        work_dir,
        "/bin/bash",
        "-c",
        "echo CHROOT_OK && "
        "cat /etc/os-release | head -5"
    ])

    print(
        "DEBUG ambiente chroot funcionando."
    )


# ==========================================================
# Pacotes
# ==========================================================

def validate_packages(package_paths):
    """
    Valida os arquivos enviados para instalação.
    """

    validated = []

    for package_path in package_paths or []:
        package_path = os.path.abspath(
            package_path
        )

        if not os.path.isfile(package_path):
            raise RuntimeError(
                f"Pacote não encontrado: {package_path}"
            )

        validated.append(
            package_path
        )

    return validated


def copy_packages_to_chroot(
    work_dir,
    package_paths
):
    """
    Copia os pacotes para dentro do sistema extraído.

    Returns:
        Tupla contendo:
            diretório físico dos pacotes
            nomes dos pacotes
    """

    package_dir = os.path.join(
        work_dir,
        "tmp",
        "packages"
    )

    run([
        "sudo",
        "mkdir",
        "-p",
        package_dir
    ])

    package_names = []

    for package_path in package_paths:
        package_name = os.path.basename(
            package_path
        )

        package_names.append(
            package_name
        )

        run([
            "sudo",
            "cp",
            package_path,
            package_dir
        ])

    return package_dir, package_names


def install_debian_packages(
    work_dir,
    package_names,
    progress_callback=None
):
    """
    Instala arquivos .deb dentro de um sistema Debian/Ubuntu.
    """

    report_progress(
        progress_callback,
        97,
        "Instalando pacotes Debian/Ubuntu..."
    )

    package_files = [
        f"/tmp/packages/{name}"
        for name in package_names
    ]

    result = run([
        "sudo",
        "chroot",
        work_dir,
        "dpkg",
        "-i",
        *package_files
    ], check=False)

    print(
        f"DEBUG código dpkg: {result.returncode}"
    )

    # O dpkg pode retornar erro por dependências.
    # apt-get -f resolve essas dependências.
    run([
        "sudo",
        "chroot",
        work_dir,
        "apt-get",
        "-f",
        "install",
        "-y"
    ])


def install_arch_packages(
    work_dir,
    package_names,
    progress_callback=None
):
    """
    Instala pacotes Arch dentro do sistema extraído.
    """

    report_progress(
        progress_callback,
        97,
        "Instalando pacotes Arch..."
    )

    package_files = [
        f"/tmp/packages/{name}"
        for name in package_names
    ]

    run([
        "sudo",
        "chroot",
        work_dir,
        "pacman",
        "-U",
        "--noconfirm",
        *package_files
    ])


def remove_temporary_packages(work_dir):
    """
    Remove os pacotes copiados temporariamente
    para dentro do sistema Live.
    """

    package_dir = os.path.join(
        work_dir,
        "tmp",
        "packages"
    )

    if os.path.exists(package_dir):
        print(
            f"DEBUG removendo pacotes temporários: "
            f"{package_dir}"
        )

        remove_path(package_dir)


# ==========================================================
# Personalização do sistema Live
# ==========================================================

def customize_live_system(
    iso_root,
    package_paths,
    progress_callback=None
):
    """
    Extrai o SquashFS e personaliza o sistema Live.

    O SquashFS não é reconstruído nesta função.

    Returns:
        Dicionário contendo:
            distro
            squashfs_path
            work_dir
    """

    iso_root = os.path.abspath(
        iso_root
    )

    package_paths = validate_packages(
        package_paths
    )

    if not package_paths:
        raise RuntimeError(
            "Nenhum pacote foi informado para personalização."
        )

    squashfs_path = detect_squashfs(
        iso_root
    )

    distro = detect_distro(
        iso_root
    )

    work_dir = os.path.join(
        iso_root,
        "squashfs-root"
    )

    print(
        f"DEBUG distribuição: {distro}"
    )

    print(
        f"DEBUG SquashFS: {squashfs_path}"
    )

    print(
        f"DEBUG diretório de trabalho: {work_dir}"
    )

    # ------------------------------------------------------
    # Remove extração anterior
    # ------------------------------------------------------

    if os.path.exists(work_dir):
        print(
            f"DEBUG removendo extração anterior: "
            f"{work_dir}"
        )

        remove_path(work_dir)

    # ------------------------------------------------------
    # Extrai SquashFS
    # ------------------------------------------------------

    report_progress(
        progress_callback,
        88,
        "Extraindo sistema interno (SquashFS)..."
    )

    run([
        "sudo",
        "unsquashfs",
        "-d",
        work_dir,
        squashfs_path
    ])

    if not os.path.isdir(work_dir):
        raise RuntimeError(
            f"Falha ao extrair SquashFS: {work_dir}"
        )

    # ------------------------------------------------------
    # Prepara chroot
    # ------------------------------------------------------

    prepare_chroot(
        work_dir
    )

    configure_dns(
        work_dir
    )

    mounted = []

    try:
        report_progress(
            progress_callback,
            90,
            "Montando ambiente chroot..."
        )

        mounted = mount_chroot_filesystems(
            work_dir
        )

        # --------------------------------------------------
        # Testa chroot
        # --------------------------------------------------

        report_progress(
            progress_callback,
            95,
            "Testando ambiente chroot..."
        )

        test_chroot(
            work_dir
        )

        # --------------------------------------------------
        # Copia pacotes
        # --------------------------------------------------

        package_dir, package_names = (
            copy_packages_to_chroot(
                work_dir,
                package_paths
            )
        )

        print(
            f"DEBUG diretório temporário: {package_dir}"
        )

        # --------------------------------------------------
        # Instala
        # --------------------------------------------------

        if distro == "debian":
            install_debian_packages(
                work_dir,
                package_names,
                progress_callback
            )

        elif distro == "arch":
            install_arch_packages(
                work_dir,
                package_names,
                progress_callback
            )

        else:
            raise RuntimeError(
                f"Distribuição não suportada: {distro}"
            )

    finally:
        report_progress(
            progress_callback,
            98,
            "Desmontando ambiente chroot..."
        )

        unmount_chroot_filesystems(
            mounted
        )

        print(
            "DEBUG ambiente chroot desmontado."
        )

    # ------------------------------------------------------
    # Remove pacotes temporários
    # ------------------------------------------------------

    remove_temporary_packages(
        work_dir
    )

    report_progress(
        progress_callback,
        98,
        "Sistema personalizado com sucesso."
    )

    print(
        "DEBUG personalização concluída."
    )

    return {
        "distro": distro,
        "squashfs_path": squashfs_path,
        "work_dir": work_dir,
    }


# ==========================================================
# Reconstrução do SquashFS
# ==========================================================

def rebuild_squashfs(
    squashfs_path,
    work_dir,
    progress_callback=None
):
    """
    Reconstrói o SquashFS.

    O novo SquashFS é criado primeiro em um arquivo temporário.
    O original somente é substituído depois que o novo arquivo
    foi criado e validado.
    """

    squashfs_path = os.path.abspath(
        squashfs_path
    )

    work_dir = os.path.abspath(
        work_dir
    )

    if not os.path.isdir(work_dir):
        raise RuntimeError(
            f"Diretório do sistema não encontrado: "
            f"{work_dir}"
        )

    report_progress(
        progress_callback,
        98,
        "Recriando sistema SquashFS..."
    )

    temp_squashfs = (
        squashfs_path
        + ".new"
    )

    if os.path.exists(temp_squashfs):
        remove_path(
            temp_squashfs
        )

    try:
        print(
            f"DEBUG criando SquashFS temporário: "
            f"{temp_squashfs}"
        )

        run([
            "sudo",
            "mksquashfs",
            work_dir,
            temp_squashfs,
            "-comp",
            "xz",
            "-noappend"
        ])

        if not os.path.isfile(
            temp_squashfs
        ):
            raise RuntimeError(
                "mksquashfs não criou o arquivo temporário."
            )

        size = os.path.getsize(
            temp_squashfs
        )

        if size <= 0:
            raise RuntimeError(
                "SquashFS temporário está vazio."
            )

        print(
            f"DEBUG novo SquashFS: "
            f"{size / (1024 ** 2):.2f} MB"
        )

        # --------------------------------------------------
        # Substitui somente depois da validação
        # --------------------------------------------------

        print(
            f"DEBUG substituindo SquashFS original: "
            f"{squashfs_path}"
        )

        run([
            "sudo",
            "mv",
            temp_squashfs,
            squashfs_path
        ])

    except Exception:
        if os.path.exists(temp_squashfs):
            remove_path(
                temp_squashfs
            )

        raise

    report_progress(
        progress_callback,
        99,
        "SquashFS recriado com sucesso."
    )

    return squashfs_path


# ==========================================================
# Detecção do Bootloader
# ==========================================================

def find_existing_file(
    iso_root,
    candidates
):
    """
    Retorna o primeiro arquivo existente da lista.
    """

    for candidate in candidates:
        path = os.path.join(
            iso_root,
            candidate
        )

        if os.path.isfile(path):
            return candidate

    return None


def detect_boot_config(iso_root):
    """
    Detecta uma estrutura de boot comum.

    Suporta estruturas baseadas em:

        Syslinux / ISOLINUX
        GRUB
    """

    iso_root = os.path.abspath(
        iso_root
    )

    efi_candidates = [
        "EFI/BOOT/BOOTX64.EFI",
        "EFI/BOOT/bootx64.efi",
        "EFI/boot/BOOTX64.EFI",
        "EFI/boot/bootx64.efi",
        "EFI/BOOT/grubx64.efi",
        "EFI/boot/grubx64.efi",
    ]

    efi_image = find_existing_file(
        iso_root,
        efi_candidates
    )

    if efi_image is None:
        raise RuntimeError(
            "Imagem EFI não encontrada na ISO."
        )

    # ======================================================
    # Syslinux / ISOLINUX
    # ======================================================

    syslinux_boot_candidates = [
        "isolinux/isolinux.bin",
        "boot/isolinux/isolinux.bin",
        "boot/syslinux/isolinux.bin",
        "syslinux/isolinux.bin",
    ]

    syslinux_catalog_candidates = [
        "isolinux/boot.cat",
        "boot/isolinux/boot.cat",
        "boot/syslinux/boot.cat",
        "syslinux/boot.cat",
    ]

    boot_image = find_existing_file(
        iso_root,
        syslinux_boot_candidates
    )

    if boot_image is not None:
        boot_catalog = find_existing_file(
            iso_root,
            syslinux_catalog_candidates
        )

        if boot_catalog is not None:
            print(
                "DEBUG bootloader: ISOLINUX/SYSLINUX"
            )

            return {
                "type": "syslinux",
                "boot_image": boot_image,
                "boot_catalog": boot_catalog,
                "efi_image": efi_image,
            }

    # ======================================================
    # GRUB
    # ======================================================

    grub_boot_candidates = [
        "boot/grub/i386-pc/eltorito.img",
        "boot/grub/eltorito.img",
    ]

    grub_boot_image = find_existing_file(
        iso_root,
        grub_boot_candidates
    )

    if grub_boot_image is not None:
        print(
            "DEBUG bootloader: GRUB"
        )

        return {
            "type": "grub",
            "boot_image": grub_boot_image,
            "boot_catalog": "boot.catalog",
            "efi_image": efi_image,
        }

    raise RuntimeError(
        "Estrutura de boot não reconhecida. "
        "Não foi encontrado um bootloader compatível."
    )


# ==========================================================
# Criação da ISO
# ==========================================================

def create_bootable_iso(
    iso_root,
    output_dir,
    progress_callback=None
):
    """
    Cria a ISO final usando xorriso.

    A ISO temporária é criada fora de iso_root para impedir
    que o xorriso tente incluir a própria ISO dentro da imagem.
    """

    iso_root = os.path.abspath(
        iso_root
    )

    output_dir = os.path.abspath(
        output_dir
    )

    ensure_directory(
        output_dir
    )

    report_progress(
        progress_callback,
        98,
        "Gerando ISO final..."
    )

    isohdpfx = find_isohdpfx()

    boot_config = detect_boot_config(
        iso_root
    )

    print(
        f"DEBUG tipo de boot: "
        f"{boot_config['type']}"
    )

    print(
        f"DEBUG boot image: "
        f"{boot_config['boot_image']}"
    )

    print(
        f"DEBUG boot catalog: "
        f"{boot_config['boot_catalog']}"
    )

    print(
        f"DEBUG EFI image: "
        f"{boot_config['efi_image']}"
    )

    # ------------------------------------------------------
    # Valida arquivos
    # ------------------------------------------------------

    boot_image_path = os.path.join(
        iso_root,
        boot_config["boot_image"]
    )

    efi_image_path = os.path.join(
        iso_root,
        boot_config["efi_image"]
    )

    if not os.path.isfile(
        boot_image_path
    ):
        raise RuntimeError(
            f"Imagem de boot não encontrada: "
            f"{boot_image_path}"
        )

    if not os.path.isfile(
        efi_image_path
    ):
        raise RuntimeError(
            f"Imagem EFI não encontrada: "
            f"{efi_image_path}"
        )

    # ------------------------------------------------------
    # Diretório temporário para a ISO
    # ------------------------------------------------------

    temp_output_dir = tempfile.mkdtemp(
        prefix="iso-output-"
    )

    temp_iso = os.path.join(
        temp_output_dir,
        "custom_linux.iso"
    )

    final_iso = os.path.join(
        output_dir,
        "custom_linux.iso"
    )

    try:
        if os.path.exists(final_iso):
            remove_path(
                final_iso
            )

        # --------------------------------------------------
        # xorriso
        # --------------------------------------------------

        command = [
            "xorriso",
            "-as",
            "mkisofs",

            # ISO filesystem
            "-r",
            "-V",
            "CustomLinux",
            "-J",
            "-l",
            "-iso-level",
            "3",

            # MBR híbrido
            "-isohybrid-mbr",
            isohdpfx,

            # BIOS / El Torito
            "-c",
            boot_config["boot_catalog"],

            "-b",
            boot_config["boot_image"],

            "-no-emul-boot",
            "-boot-load-size",
            "4",
            "-boot-info-table",

            # UEFI
            "-eltorito-alt-boot",
            "-e",
            boot_config["efi_image"],
            "-no-emul-boot",

            # GPT híbrido
            "-isohybrid-gpt-basdat",

            # Saída
            "-o",
            temp_iso,

            # Conteúdo
            iso_root,
        ]

        print()
        print(
            "DEBUG comando xorriso:"
        )

        print(
            " ".join(
                f'"{arg}"' if " " in arg else arg
                for arg in command
            )
        )

        print()

        run(command)

        # --------------------------------------------------
        # Validação
        # --------------------------------------------------

        if not os.path.isfile(
            temp_iso
        ):
            raise RuntimeError(
                "O xorriso terminou, "
                "mas a ISO não foi criada."
            )

        iso_size = os.path.getsize(
            temp_iso
        )

        if iso_size <= 0:
            raise RuntimeError(
                "A ISO criada está vazia."
            )

        print(
            f"DEBUG ISO temporária criada: "
            f"{temp_iso}"
        )

        print(
            f"DEBUG tamanho da ISO: "
            f"{iso_size / (1024 ** 2):.2f} MB"
        )

        # --------------------------------------------------
        # Move para o destino final
        # --------------------------------------------------

        shutil.move(
            temp_iso,
            final_iso
        )

    finally:
        shutil.rmtree(
            temp_output_dir,
            ignore_errors=True
        )

    print(
        f"ISO criada com sucesso: "
        f"{final_iso}"
    )

    report_progress(
        progress_callback,
        100,
        "ISO criada com sucesso!"
    )

    return final_iso


# ==========================================================
# Limpeza do diretório de trabalho
# ==========================================================

def clean_output_directory(out_dir):
    """
    Remove todo o conteúdo anterior do diretório de trabalho.
    """

    if not os.path.isdir(out_dir):
        return

    for entry in os.listdir(out_dir):
        path = os.path.join(
            out_dir,
            entry
        )

        print(
            f"DEBUG removendo: {path}"
        )

        remove_path(path)


# ==========================================================
# Função principal
# ==========================================================

def build_iso(
    iso_path,
    out_dir,
    progress_callback=None,
    deb_paths=None
):
    """
    Executa o processo completo de criação da ISO.

    Fluxo:

        ISO original
            ↓
        Extração
            ↓
        Detecção do SquashFS
            ↓
        Extração do sistema
            ↓
        Chroot
            ↓
        Instalação dos pacotes
            ↓
        Desmontagem
            ↓
        Reconstrução do SquashFS
            ↓
        Reconstrução da ISO
            ↓
        ISO final
    """

    iso_path = os.path.abspath(
        iso_path
    )

    out_dir = os.path.abspath(
        out_dir
    )

    # ------------------------------------------------------
    # Valida ISO
    # ------------------------------------------------------

    if not os.path.isfile(
        iso_path
    ):
        raise RuntimeError(
            f"ISO não encontrada: {iso_path}"
        )

    # O diretório de saída não pode ser a própria ISO.
    if os.path.isfile(out_dir):
        raise RuntimeError(
            "O caminho de saída informado é um arquivo, "
            "não um diretório."
        )

    # ------------------------------------------------------
    # Dependências
    # ------------------------------------------------------

    validate_dependencies()

    # ------------------------------------------------------
    # Prepara diretório
    # ------------------------------------------------------

    ensure_directory(
        out_dir
    )

    report_progress(
        progress_callback,
        5,
        "Preparando processo..."
    )

    print(
        f"DEBUG ISO original: {iso_path}"
    )

    print(
        f"DEBUG diretório de trabalho: {out_dir}"
    )

    # ------------------------------------------------------
    # Limpa saída anterior
    # ------------------------------------------------------

    clean_output_directory(
        out_dir
    )

    # ------------------------------------------------------
    # Extrai ISO
    # ------------------------------------------------------

    report_progress(
        progress_callback,
        10,
        "Extraindo ISO..."
    )

    extract_with_7z(
        iso_path,
        out_dir
    )

    print(
        "DEBUG ISO extraída com 7z."
    )

    # ------------------------------------------------------
    # Personalização
    # ------------------------------------------------------

    if deb_paths:
        result = customize_live_system(
            out_dir,
            deb_paths,
            progress_callback
        )

        rebuild_squashfs(
            result["squashfs_path"],
            result["work_dir"],
            progress_callback
        )

        # O diretório squashfs-root não pertence mais ao
        # conteúdo final da ISO e não deve ser incluído.
        if os.path.exists(
            result["work_dir"]
        ):
            remove_path(
                result["work_dir"]
            )

    # ------------------------------------------------------
    # Criação da ISO
    # ------------------------------------------------------

    final_iso = create_bootable_iso(
        out_dir,
        out_dir,
        progress_callback
    )

    report_progress(
        progress_callback,
        100,
        "Finalizado!"
    )

    return {
        "method": "iso",
        "status": "ok",
        "path": final_iso,
    }