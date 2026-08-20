import os
import platform
import shutil
import subprocess
import tempfile
import hashlib
import re


# ==========================================================
# Utilidades
# ==========================================================

def report_progress(progress_callback, percent, message):
    """
    Envia atualização de progresso caso exista callback.
    """

    if progress_callback:
        progress_callback(percent, message)


def require_root():
    """
    O processo de criação da ISO precisa estar executando
    como root.

    IMPORTANTE:
        Este arquivo NÃO usa sudo internamente.

    Isso evita que o Flask fique parado esperando uma senha
    no meio do processo de criação da ISO.
    """

    if os.name != "posix":
        raise RuntimeError(
            "A criação da ISO exige um sistema POSIX/Linux."
        )

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        raise PermissionError(
            "O processo de criação da ISO precisa ser executado "
            "como root.\n\n"
            "Inicie o backend com:\n"
            "    sudo python3 app.py\n\n"
            "ou, usando o ambiente virtual:\n"
            "    sudo ./venv/bin/python app.py\n\n"
            "O iso_linux.py não executa sudo internamente."
        )


def run(
    cmd,
    check=True,
    allow_codes=(0,),
    capture_output=False,
    text=False
):
    """
    Executa um comando externo.

    Args:
        cmd: lista contendo comando e argumentos.
        check: se True, gera exceção para códigos não permitidos.
        allow_codes: código ou coleção de códigos aceitos.
        capture_output: captura stdout/stderr.
        text: retorna saída como texto.

    Returns:
        subprocess.CompletedProcess
    """

    cmd = [str(arg) for arg in cmd]

    print("+", " ".join(cmd))

    if isinstance(allow_codes, int):
        allowed_codes = {allow_codes}
    else:
        allowed_codes = set(allow_codes)

    result = subprocess.run(
        cmd,
        capture_output=capture_output,
        text=text
    )

    if check and result.returncode not in allowed_codes:
        raise subprocess.CalledProcessError(
            result.returncode,
            cmd,
            output=result.stdout,
            stderr=result.stderr
        )

    return result


def has_executable(name):
    """
    Verifica se um executável existe no PATH.
    """

    return shutil.which(name) is not None


def is_mounted(path):
    """
    Verifica se um caminho está atualmente montado, lendo
    /proc/mounts diretamente (não depende de ferramentas
    externas como findmnt).

    Usado para confirmar que um "umount" realmente funcionou
    antes de seguir para o mksquashfs -- ver comentário em
    unmount_chroot_filesystems() para o motivo disso importar.
    """

    real_path = os.path.realpath(path)

    try:
        with open("/proc/mounts", "r", encoding="utf-8") as mounts_file:
            for line in mounts_file:
                fields = line.split()

                if len(fields) >= 2:
                    mount_point = fields[1].encode(
                        "utf-8"
                    ).decode("unicode_escape")

                    if os.path.realpath(mount_point) == real_path:
                        return True

    except OSError:
        return False

    return False


def remove_path(path):
    """
    Remove arquivo, link ou diretório.

    NÃO usa sudo.

    O processo inteiro deve estar executando como root.
    """

    if not os.path.lexists(path):
        return

    if os.path.isdir(path) and not os.path.islink(path):
        run([
            "rm",
            "-rf",
            path
        ])
    else:
        run([
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
        "mount",
        "umount",
        "chroot",
        "rsync",
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
    Procura pelo isohdpfx.bin usado na criação de ISOs
    híbridas BIOS/MBR.

    Usado somente no fluxo Debian/Ubuntu.
    """

    possible_paths = [
        "/usr/lib/ISOLINUX/isohdpfx.bin",
        "/usr/lib/syslinux/isohdpfx.bin",
        "/usr/lib/syslinux/bios/isohdpfx.bin",
        "/usr/share/syslinux/isohdpfx.bin",
        "/usr/share/syslinux/bios/isohdpfx.bin",
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

def extract_iso(iso_path, out_dir):
    """
    Extrai o conteúdo completo da ISO usando xorriso (osirrox).

    Por que trocar o 7z pelo xorriso:
        O 7z, ao extrair um filesystem ISO9660/Rock Ridge, não
        preserva de forma confiável symlinks, permissões e
        atributos especiais -- em alguns casos um symlink vira
        um arquivo comum contendo o caminho de destino como
        texto. Isso é particularmente arriscado para a estrutura
        de boot EFI, que depende de arquivos exatamente como
        estão no disco original. O xorriso -osirrox é a mesma
        ferramenta usada para regravar a ISO, então extrai com
        fidelidade total.
    """

    iso_path = os.path.abspath(iso_path)
    out_dir = os.path.abspath(out_dir)

    if not os.path.isfile(iso_path):
        raise RuntimeError(
            f"ISO não encontrada: {iso_path}"
        )

    ensure_directory(out_dir)

    run([
        "xorriso",
        "-osirrox", "on",
        "-indev", iso_path,
        "-extract", "/", out_dir,
    ])


def extract_with_7z(iso_path, out_dir):
    """
    Extrai o conteúdo da ISO usando 7z.

    Mantido como fallback caso o xorriso falhe na extração
    (ver extract_iso). Não é mais o método primário.
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

    NÃO usa sudo.
    """

    if platform.system() != "Linux":
        raise RuntimeError(
            "A montagem de ISO está disponível somente no Linux."
        )

    require_root()

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

        run([
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

            run([
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


def detect_squashfs_compression(squashfs_path):
    """
    Detecta a compressão usada pelo SquashFS original.
    """

    result = run(
        [
            "unsquashfs",
            "-s",
            squashfs_path
        ],
        capture_output=True,
        text=True
    )

    output = (
        (result.stdout or "")
        + "\n"
        + (result.stderr or "")
    )

    match = re.search(
        r"Compression\s+([A-Za-z0-9_-]+)",
        output,
        re.IGNORECASE
    )

    if match:
        compression = match.group(1).lower()

        allowed = {
            "xz",
            "zstd",
            "gzip",
            "lzo",
            "lz4",
            "lzma",
        }

        if compression in allowed:
            print(
                f"DEBUG compressão SquashFS detectada: "
                f"{compression}"
            )

            return compression

    print(
        "WARNING: não foi possível detectar a compressão "
        "do SquashFS. Usando xz."
    )

    return "xz"


# ==========================================================
# Chroot
# ==========================================================

def prepare_chroot(work_dir):
    """
    Cria os diretórios necessários para o ambiente chroot.

    NÃO usa sudo.
    """

    require_root()

    directories = [
        os.path.join(work_dir, "dev"),
        os.path.join(work_dir, "proc"),
        os.path.join(work_dir, "sys"),
        os.path.join(work_dir, "run"),
    ]

    for directory in directories:
        run([
            "mkdir",
            "-p",
            directory
        ])


def configure_dns(work_dir):
    """
    Copia o resolv.conf do sistema hospedeiro para dentro do
    sistema Live, TEMPORARIAMENTE, apenas para que a instalação
    de pacotes dentro do chroot tenha resolução de DNS.

    Antes de sobrescrever, guarda o estado original (arquivo
    normal, symlink, ou inexistente) para que restore_dns()
    possa desfazer isso depois. Sem essa restauração, a ISO
    final seria distribuída com a configuração de DNS do
    computador usado para o build, o que não faz sentido para
    quem for usar a ISO em outra rede.
    """

    require_root()

    resolv_conf = os.path.join(
        work_dir,
        "etc",
        "resolv.conf"
    )

    state = {
        "existed": False,
        "is_symlink": False,
        "target": None,
        "backup": None,
    }

    try:
        if os.path.islink(resolv_conf):
            state["existed"] = True
            state["is_symlink"] = True
            state["target"] = os.readlink(resolv_conf)

        elif os.path.isfile(resolv_conf):
            state["existed"] = True

            backup_path = resolv_conf + ".isobuilder-backup"

            shutil.copy2(
                resolv_conf,
                backup_path
            )

            state["backup"] = backup_path

        run([
            "rm",
            "-f",
            resolv_conf
        ], check=False)

        run([
            "cp",
            "/etc/resolv.conf",
            resolv_conf
        ])

        print(
            "DEBUG DNS configurado temporariamente."
        )

    except Exception as exc:
        print(
            f"WARNING: não foi possível configurar DNS: {exc}"
        )

    return state


def restore_dns(work_dir, state):
    """
    Restaura o /etc/resolv.conf original do sistema Live,
    desfazendo configure_dns().
    """

    if not state:
        return

    resolv_conf = os.path.join(
        work_dir,
        "etc",
        "resolv.conf"
    )

    try:
        run([
            "rm",
            "-f",
            resolv_conf
        ], check=False)

        if state.get("is_symlink") and state.get("target"):
            os.symlink(
                state["target"],
                resolv_conf
            )

        elif state.get("backup") and os.path.isfile(state["backup"]):
            shutil.move(
                state["backup"],
                resolv_conf
            )

        # Se não existia antes, simplesmente permanece removido.

        print(
            "DEBUG DNS original do sistema Live restaurado."
        )

    except Exception as exc:
        print(
            f"WARNING: não foi possível restaurar o DNS original: {exc}"
        )


def cleanup_chroot_traces(work_dir, distro):
    """
    Remove vestígios deixados pela instalação de pacotes dentro
    do chroot, antes de desmontar e recriar o SquashFS:

    - locks do dpkg/pacman que, se sobrarem, impedem o
      gerenciador de pacotes de funcionar no sistema Live
      já inicializado;
    - reseta /etc/machine-id (deixando o arquivo vazio), que é
      o mecanismo padrão do systemd para gerar um ID novo no
      primeiro boot. Sem isso, toda cópia da ISO gerada
      compartilharia o mesmo machine-id, o que causa conflitos
      de D-Bus/NetworkManager/journal quando mais de uma
      instância roda na mesma rede.
    """

    require_root()

    machine_id_path = os.path.join(
        work_dir,
        "etc",
        "machine-id"
    )

    if os.path.isfile(machine_id_path):
        try:
            with open(machine_id_path, "w", encoding="utf-8"):
                pass

            print(
                "DEBUG machine-id resetado para o primeiro boot."
            )

        except OSError as exc:
            print(
                f"WARNING: não foi possível resetar machine-id: {exc}"
            )

    if distro == "debian":
        lock_candidates = [
            os.path.join(work_dir, "var", "lib", "dpkg", "lock"),
            os.path.join(work_dir, "var", "lib", "dpkg", "lock-frontend"),
            os.path.join(work_dir, "var", "cache", "apt", "archives", "lock"),
        ]

    elif distro == "arch":
        lock_candidates = [
            os.path.join(work_dir, "var", "lib", "pacman", "db.lck"),
        ]

    else:
        lock_candidates = []

    for lock_path in lock_candidates:
        if os.path.exists(lock_path):
            print(
                f"DEBUG removendo lock residual: {lock_path}"
            )

            remove_path(lock_path)


def mount_chroot_filesystems(work_dir):
    """
    Monta os pseudo-filesystems necessários para o chroot.

    NÃO usa sudo.
    """

    require_root()

    dev_dir = os.path.join(work_dir, "dev")
    proc_dir = os.path.join(work_dir, "proc")
    sys_dir = os.path.join(work_dir, "sys")
    run_dir = os.path.join(work_dir, "run")

    mounted = []

    try:
        # --------------------------------------------------
        # /dev
        # --------------------------------------------------

        run([
            "mount",
            "--rbind",
            "/dev",
            dev_dir
        ])

        run([
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
            "mount",
            "--rbind",
            "/sys",
            sys_dir
        ])

        run([
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
            "mount",
            "--rbind",
            "/run",
            run_dir
        ])

        run([
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
    Desmonta os pseudo-filesystems do chroot na ordem inversa
    da montagem.

    NÃO usa sudo.

    IMPORTANTE (correção de bug):
        Na versão anterior, "umount" era chamado com
        check=False e o resultado era simplesmente ignorado.
        Se algum hook de pós-instalação (muito comum no
        pacman -- ex.: mkinitcpio, gpg-agent) deixasse um
        processo vivo usando /dev, /proc, /sys ou /run, o
        umount falhava e o código seguia em frente mesmo
        assim, SEM AVISAR. O mksquashfs seguinte então
        empacotava o conteúdo REAL do sistema hospedeiro
        (montado via bind) dentro do SquashFS final -- gerando
        uma ISO corrompida/gigante de forma intermitente e
        sem erro visível. Esse é um dos motivos mais prováveis
        de "o Arch às vezes falha sem explicação".

        Agora: confirma que cada ponto realmente foi
        desmontado (lendo /proc/mounts), tenta "umount -l"
        (lazy) como segunda tentativa, e se mesmo assim
        continuar montado, lança uma exceção em vez de
        continuar silenciosamente.
    """

    require_root()

    failures = []

    for mount_type, mount_point in reversed(mounted):
        if mount_type == "recursive":
            run(["umount", "-R", mount_point], check=False)
        else:
            run(["umount", mount_point], check=False)

        if is_mounted(mount_point):
            print(
                f"WARNING: {mount_point} continua montado após "
                f"umount normal, tentando desmonte lazy (-l)..."
            )

            run(["umount", "-l", mount_point], check=False)

        if is_mounted(mount_point):
            failures.append(mount_point)

    if failures:
        raise RuntimeError(
            "Falha ao desmontar os seguintes pontos do chroot: "
            + ", ".join(failures)
            + ". O processo foi interrompido para evitar gerar "
            "um SquashFS corrompido com arquivos do sistema "
            "hospedeiro. Verifique processos presos com "
            "'lsof +D <caminho>' ou 'fuser -vm <caminho>' e "
            "tente novamente."
        )


def test_chroot(work_dir):
    """
    Testa se o sistema extraído consegue iniciar um chroot.
    """

    require_root()

    run([
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
    """

    require_root()

    package_dir = os.path.join(
        work_dir,
        "tmp",
        "packages"
    )

    run([
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

    require_root()

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
        "chroot",
        work_dir,
        "dpkg",
        "-i",
        *package_files
    ], check=False)

    print(
        f"DEBUG código dpkg: {result.returncode}"
    )

    run([
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

    require_root()

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
        "chroot",
        work_dir,
        "pacman",
        "-U",
        "--noconfirm",
        *package_files
    ])


def remove_temporary_packages(work_dir):
    """
    Remove os pacotes temporários.
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
# Personalização
# ==========================================================

def customize_live_system(
    iso_root,
    package_paths,
    progress_callback=None
):
    """
    Extrai o SquashFS e personaliza o sistema Live.
    """

    require_root()

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

    squashfs_compression = (
        detect_squashfs_compression(
            squashfs_path
        )
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
        f"DEBUG compressão SquashFS: "
        f"{squashfs_compression}"
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
        "unsquashfs",
        "-d",
        work_dir,
        squashfs_path
    ])

    if not os.path.isdir(work_dir):
        raise RuntimeError(
            f"Falha ao extrair SquashFS: {work_dir}"
        )

    prepare_chroot(
        work_dir
    )

    dns_state = configure_dns(
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

        report_progress(
            progress_callback,
            95,
            "Testando ambiente chroot..."
        )

        test_chroot(
            work_dir
        )

        package_dir, package_names = (
            copy_packages_to_chroot(
                work_dir,
                package_paths
            )
        )

        print(
            f"DEBUG diretório temporário: {package_dir}"
        )

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

        # ----------------------------------------------
        # Limpeza de vestígios (locks, machine-id) e
        # restauração do DNS ainda com os filesystems
        # montados, antes de desmontar.
        # ----------------------------------------------

        cleanup_chroot_traces(
            work_dir,
            distro
        )

        restore_dns(
            work_dir,
            dns_state
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
        "squashfs_compression": squashfs_compression,
        "work_dir": work_dir,
    }


# ==========================================================
# Reconstrução do SquashFS
# ==========================================================

def rebuild_squashfs(
    squashfs_path,
    work_dir,
    progress_callback=None,
    compression=None
):
    """
    Reconstrói o SquashFS preservando a compressão original.
    """

    require_root()

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

    # ----------------------------------------------------------
    # Rede de segurança: mesmo que unmount_chroot_filesystems()
    # já garanta (e agora valide de fato) que dev/proc/sys/run
    # foram desmontados, excluímos esses caminhos explicitamente
    # do mksquashfs. Isso é redundante por design -- é barato e
    # evita que um lapso nesse processo empacote arquivos do
    # sistema hospedeiro dentro da ISO final.
    # ----------------------------------------------------------

    for sensitive_dir in ("dev", "proc", "sys", "run"):
        full_path = os.path.join(work_dir, sensitive_dir)

        if is_mounted(full_path):
            raise RuntimeError(
                f"{full_path} ainda está montado. Abortando a "
                "reconstrução do SquashFS para não empacotar "
                "arquivos do sistema hospedeiro dentro da ISO."
            )

    compression = (
        compression
        or detect_squashfs_compression(
            squashfs_path
        )
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

        print(
            f"DEBUG usando compressão SquashFS: "
            f"{compression}"
        )

        run([
            "mksquashfs",
            work_dir,
            temp_squashfs,
            "-comp",
            compression,
            "-noappend",
            "-wildcards",
            "-e",
            "dev/*",
            "proc/*",
            "sys/*",
            "run/*",
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

        run([
            "unsquashfs",
            "-s",
            temp_squashfs
        ])

        print(
            f"DEBUG substituindo SquashFS original: "
            f"{squashfs_path}"
        )

        run([
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
# Arquivos auxiliares do Arch
# ==========================================================

def update_arch_squashfs_checksum(
    iso_root,
    squashfs_path
):
    """
    Atualiza o SHA-512 do airootfs.sfs da ISO Arch.
    """

    relative_squashfs = os.path.relpath(
        squashfs_path,
        iso_root
    ).replace(
        os.sep,
        "/"
    )

    checksum_path = os.path.join(
        iso_root,
        "arch",
        "x86_64",
        "airootfs.sha512"
    )

    ensure_directory(
        os.path.dirname(checksum_path)
    )

    sha512 = hashlib.sha512()

    with open(
        squashfs_path,
        "rb"
    ) as source:
        while True:
            chunk = source.read(
                1024 * 1024
            )

            if not chunk:
                break

            sha512.update(chunk)

    digest = sha512.hexdigest()

    with open(
        checksum_path,
        "w",
        encoding="utf-8"
    ) as checksum_file:
        checksum_file.write(
            f"{digest}  {relative_squashfs}\n"
        )

    print(
        f"DEBUG checksum SHA-512 atualizado: "
        f"{checksum_path}"
    )

    return checksum_path


def remove_arch_backup_files(iso_root):
    """
    Remove backups antigos do airootfs.sfs.
    """

    candidates = [
        os.path.join(
            iso_root,
            "arch",
            "x86_64",
            "airootfs.sfs.backup"
        ),
    ]

    for path in candidates:
        if os.path.exists(path):
            print(
                f"DEBUG removendo backup do Arch: {path}"
            )

            remove_path(path)


# ==========================================================
# Boot Debian / Ubuntu
# ==========================================================

def find_existing_file(
    iso_root,
    candidates
):
    """
    Retorna o primeiro arquivo existente.
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
    Detecta estrutura de boot usada pelo fluxo Debian/Ubuntu.
    """

    iso_root = os.path.abspath(
        iso_root
    )

    efi_candidates = [
        "EFI/BOOT/BOOTX64.EFI",
        "EFI/BOOT/BOOTx64.EFI",
        "EFI/BOOT/bootx64.efi",
        "EFI/boot/BOOTX64.EFI",
        "EFI/boot/BOOTx64.EFI",
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

    # --------------------------------------------------------
    # syslinux/isolinux
    #
    # CORREÇÃO DE BUG: a versão anterior exigia que o arquivo
    # de catálogo de boot (ex.: "isolinux/boot.cat") já
    # existisse fisicamente na ISO extraída para reconhecer o
    # bootloader. Só que esse caminho é apenas o DESTINO que o
    # xorriso vai escrever com "-c" -- ele não precisa
    # pré-existir. Isso causava falsos negativos ("Estrutura de
    # boot não reconhecida") em ISOs Debian/Ubuntu válidas que
    # não carregavam um boot.cat físico. Agora a imagem de boot
    # e o catálogo são pareados pelo mesmo diretório, e só a
    # imagem de boot precisa existir de fato.
    # --------------------------------------------------------

    syslinux_pairs = [
        ("isolinux/isolinux.bin", "isolinux/boot.cat"),
        ("boot/isolinux/isolinux.bin", "boot/isolinux/boot.cat"),
        ("boot/syslinux/isolinux.bin", "boot/syslinux/boot.cat"),
        ("syslinux/isolinux.bin", "syslinux/boot.cat"),
    ]

    for boot_candidate, catalog_candidate in syslinux_pairs:
        boot_path = os.path.join(iso_root, boot_candidate)

        if os.path.isfile(boot_path):
            print(
                "DEBUG bootloader: ISOLINUX/SYSLINUX"
            )

            return {
                "type": "syslinux",
                "boot_image": boot_candidate,
                "boot_catalog": catalog_candidate,
                "efi_image": efi_image,
            }

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
        "Estrutura de boot não reconhecida."
    )


# ==========================================================
# Criação ISO Debian / Ubuntu
# ==========================================================

def create_debian_bootable_iso(
    iso_root,
    output_dir,
    progress_callback=None
):
    """
    Mantém o fluxo tradicional Debian/Ubuntu.
    """

    require_root()

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
            remove_path(final_iso)

        command = [
            "xorriso",
            "-as",
            "mkisofs",

            "-r",
            "-V",
            "CustomLinux",
            "-J",
            "-l",
            "-iso-level",
            "3",

            "-isohybrid-mbr",
            isohdpfx,

            "-c",
            boot_config["boot_catalog"],

            "-b",
            boot_config["boot_image"],

            "-no-emul-boot",
            "-boot-load-size",
            "4",
            "-boot-info-table",

            "-eltorito-alt-boot",
            "-e",
            boot_config["efi_image"],
            "-no-emul-boot",

            "-isohybrid-gpt-basdat",

            "-o",
            temp_iso,

            iso_root,
        ]

        print(
            "DEBUG comando xorriso Debian/Ubuntu:"
        )

        print(
            " ".join(command)
        )

        run(command)

        if not os.path.isfile(temp_iso):
            raise RuntimeError(
                "O xorriso terminou, mas a ISO não foi criada."
            )

        iso_size = os.path.getsize(temp_iso)

        if iso_size <= 0:
            raise RuntimeError(
                "A ISO criada está vazia."
            )

        print(
            f"DEBUG tamanho da ISO: "
            f"{iso_size / (1024 ** 2):.2f} MB"
        )

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
        f"ISO criada com sucesso: {final_iso}"
    )

    report_progress(
        progress_callback,
        100,
        "ISO criada com sucesso!"
    )

    return final_iso


# ==========================================================
# Criação ISO Arch
# ==========================================================

def create_arch_bootable_iso(
    original_iso,
    iso_root,
    squashfs_path,
    output_dir,
    progress_callback=None
):
    """
    Cria uma ISO Arch preservando integralmente o boot
    da ISO original através do xorriso replay.

    NÃO reconstrói manualmente:
        MBR
        GPT
        El Torito
        EFI
    """

    require_root()

    original_iso = os.path.abspath(
        original_iso
    )

    iso_root = os.path.abspath(
        iso_root
    )

    squashfs_path = os.path.abspath(
        squashfs_path
    )

    output_dir = os.path.abspath(
        output_dir
    )

    if not os.path.isfile(original_iso):
        raise RuntimeError(
            f"ISO original não encontrada: {original_iso}"
        )

    if not os.path.isfile(squashfs_path):
        raise RuntimeError(
            f"SquashFS personalizado não encontrado: "
            f"{squashfs_path}"
        )

    ensure_directory(
        output_dir
    )

    report_progress(
        progress_callback,
        99,
        "Reconstruindo ISO Arch preservando o boot original..."
    )

    temp_output_dir = tempfile.mkdtemp(
        prefix="arch-iso-output-"
    )

    temp_iso = os.path.join(
        temp_output_dir,
        "custom_arch.iso"
    )

    final_iso = os.path.join(
        output_dir,
        "custom_linux.iso"
    )

    squashfs_iso_path = (
        "/arch/x86_64/airootfs.sfs"
    )

    checksum_path = os.path.join(
        iso_root,
        "arch",
        "x86_64",
        "airootfs.sha512"
    )

    try:
        if os.path.exists(final_iso):
            remove_path(final_iso)

        command = [
            "xorriso",

            "-indev",
            original_iso,

            "-outdev",
            temp_iso,

            "-map",
            squashfs_path,
            squashfs_iso_path,
        ]

        if os.path.isfile(checksum_path):
            command.extend([
                "-map",
                checksum_path,
                "/arch/x86_64/airootfs.sha512",
            ])

        command.extend([
            "-boot_image",
            "any",
            "replay",

            "-commit",
            "-end",
        ])

        print(
            "DEBUG comando xorriso Arch:"
        )

        print(
            " ".join(command)
        )

        run(command)

        if not os.path.isfile(temp_iso):
            raise RuntimeError(
                "O xorriso terminou, mas a ISO Arch não foi criada."
            )

        iso_size = os.path.getsize(temp_iso)

        if iso_size <= 0:
            raise RuntimeError(
                "A ISO Arch criada está vazia."
            )

        print(
            f"DEBUG tamanho da ISO Arch: "
            f"{iso_size / (1024 ** 3):.2f} GB"
        )

        # --------------------------------------------------
        # Validação do boot
        # --------------------------------------------------

        report_progress(
            progress_callback,
            99,
            "Validando boot da ISO Arch..."
        )

        validation = subprocess.run(
            [
                "xorriso",
                "-indev",
                temp_iso,
                "-report_el_torito",
                "plain",
                "-report_system_area",
                "plain",
            ],
            capture_output=True,
            text=True
        )

        validation_output = (
            (validation.stdout or "")
            + "\n"
            + (validation.stderr or "")
        )

        print(
            "DEBUG validação El Torito/System Area:"
        )

        print(
            validation_output
        )

        if validation.returncode != 0:
            raise RuntimeError(
                "Falha ao validar a estrutura de boot "
                "da ISO Arch."
            )

        if "El Torito" not in validation_output:
            raise RuntimeError(
                "A ISO Arch resultante não apresenta "
                "informações El Torito."
            )

        if "UEFI" not in validation_output:
            raise RuntimeError(
                "A ISO Arch resultante não apresenta "
                "uma entrada UEFI no El Torito."
            )

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
        f"ISO Arch criada com sucesso: {final_iso}"
    )

    report_progress(
        progress_callback,
        100,
        "ISO Arch criada com sucesso!"
    )

    return final_iso


# ==========================================================
# Seleção do método
# ==========================================================

def create_bootable_iso(
    iso_root,
    output_dir,
    progress_callback=None,
    distro=None,
    original_iso=None,
    squashfs_path=None
):
    """
    Seleciona o método correto de criação da ISO.
    """

    if distro == "arch":
        if not original_iso:
            raise RuntimeError(
                "A ISO original é obrigatória para criar "
                "uma ISO Arch usando replay."
            )

        if not squashfs_path:
            raise RuntimeError(
                "O caminho do SquashFS é obrigatório."
            )

        return create_arch_bootable_iso(
            original_iso,
            iso_root,
            squashfs_path,
            output_dir,
            progress_callback
        )

    if distro == "debian":
        return create_debian_bootable_iso(
            iso_root,
            output_dir,
            progress_callback
        )

    raise RuntimeError(
        f"Distribuição não suportada para criação da ISO: "
        f"{distro}"
    )


# ==========================================================
# Limpeza
# ==========================================================

def clean_output_directory(out_dir):
    """
    Remove todo o conteúdo anterior do diretório de trabalho.

    NÃO usa sudo.
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

    O processo inteiro deve ser iniciado como root.

    Arch:
        mantém o boot original através de xorriso replay.

    Debian/Ubuntu:
        mantém o fluxo tradicional mkisofs.
    """

    # ------------------------------------------------------
    # PRIMEIRA COISA:
    # verifica root antes de fazer qualquer trabalho.
    # ------------------------------------------------------

    require_root()

    iso_path = os.path.abspath(
        iso_path
    )

    out_dir = os.path.abspath(
        out_dir
    )

    if not os.path.isfile(iso_path):
        raise RuntimeError(
            f"ISO não encontrada: {iso_path}"
        )

    if os.path.isfile(out_dir):
        raise RuntimeError(
            "O caminho de saída informado é um arquivo, "
            "não um diretório."
        )

    validate_dependencies()

    ensure_directory(
        out_dir
    )

    report_progress(
        progress_callback,
        5,
        "Preparando processo..."
    )

    # ------------------------------------------------------
    # CORREÇÃO DE BUG: clean_output_directory() logo abaixo
    # apaga TUDO dentro de out_dir. Se a ISO de entrada estiver
    # dentro do próprio out_dir (ex.: usuário aponta o mesmo
    # diretório para entrada e saída), a ISO original seria
    # apagada antes de ser extraída. Copiamos para um diretório
    # temporário seguro nesse caso.
    # ------------------------------------------------------

    temp_input_dir = None

    if iso_path == out_dir or iso_path.startswith(out_dir + os.sep):
        temp_input_dir = tempfile.mkdtemp(prefix="iso-input-")

        safe_iso_path = os.path.join(
            temp_input_dir,
            os.path.basename(iso_path)
        )

        shutil.copy2(iso_path, safe_iso_path)

        print(
            "WARNING: a ISO de entrada estava dentro do diretório "
            f"de saída. Copiada temporariamente para: {safe_iso_path}"
        )

        iso_path = safe_iso_path

    print(
        f"DEBUG ISO original: {iso_path}"
    )

    print(
        f"DEBUG diretório de trabalho: {out_dir}"
    )

    clean_output_directory(
        out_dir
    )

    # ------------------------------------------------------
    # Extração da ISO
    # ------------------------------------------------------

    report_progress(
        progress_callback,
        10,
        "Extraindo ISO..."
    )

    try:
        extract_iso(
            iso_path,
            out_dir
        )

        print(
            "DEBUG ISO extraída com xorriso (osirrox)."
        )

    except Exception as exc:
        print(
            f"WARNING: falha ao extrair com xorriso ({exc}); "
            "tentando 7z como alternativa..."
        )

        clean_output_directory(
            out_dir
        )

        extract_with_7z(
            iso_path,
            out_dir
        )

        print(
            "DEBUG ISO extraída com 7z (fallback)."
        )

    finally:
        if temp_input_dir:
            shutil.rmtree(
                temp_input_dir,
                ignore_errors=True
            )

    # ------------------------------------------------------
    # Detecção da distribuição
    # ------------------------------------------------------

    distro = detect_distro(
        out_dir
    )

    print(
        f"DEBUG distribuição detectada no build: "
        f"{distro}"
    )

    result = None

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
            progress_callback,
            result.get("squashfs_compression")
        )

        # --------------------------------------------------
        # Arch: atualiza checksum
        # --------------------------------------------------

        if distro == "arch":
            update_arch_squashfs_checksum(
                out_dir,
                result["squashfs_path"]
            )

            remove_arch_backup_files(
                out_dir
            )

        # --------------------------------------------------
        # Remove squashfs-root
        # --------------------------------------------------

        if os.path.exists(
            result["work_dir"]
        ):
            remove_path(
                result["work_dir"]
            )

    else:
        if distro == "arch":
            remove_arch_backup_files(
                out_dir
            )

    # ------------------------------------------------------
    # Criação da ISO
    # ------------------------------------------------------

    if distro == "arch":

        if result is None:
            report_progress(
                progress_callback,
                99,
                "Nenhuma personalização solicitada. "
                "Copiando ISO Arch original..."
            )

            final_iso = os.path.join(
                out_dir,
                "custom_linux.iso"
            )

            if os.path.exists(final_iso):
                remove_path(
                    final_iso
                )

            shutil.copy2(
                iso_path,
                final_iso
            )

            report_progress(
                progress_callback,
                100,
                "ISO criada com sucesso!"
            )

        else:
            final_iso = create_bootable_iso(
                out_dir,
                out_dir,
                progress_callback,
                distro="arch",
                original_iso=iso_path,
                squashfs_path=result["squashfs_path"]
            )

    else:
        final_iso = create_bootable_iso(
            out_dir,
            out_dir,
            progress_callback,
            distro="debian",
            original_iso=iso_path,
            squashfs_path=(
                result["squashfs_path"]
                if result
                else None
            )
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
        "distro": distro,
    }