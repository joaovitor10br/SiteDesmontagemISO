🐧 ISO Builder Web

Ferramenta web local para extração, modificação e reconstrução de imagens ISO Linux de forma simples e visual.

O projeto permite gerar ISOs personalizadas adicionando pacotes .deb automaticamente e recriando a imagem bootável (BIOS + UEFI).

✨ Demonstração

Interface web para gerar uma ISO personalizada:

Upload da ISO original
Inclusão de pacotes .deb
Barra de progresso em tempo real
Geração automática de ISO bootável
🚀 Funcionalidades

✔ Extração automática da ISO Linux
✔ Montagem via loop + rsync
✔ Customização do sistema via chroot
✔ Instalação automática de pacotes .deb
✔ Reconstrução do SquashFS
✔ Geração de ISO bootável (BIOS + UEFI) com xorriso
✔ Detecção automática do isohdpfx (Syslinux)
✔ Interface web com barra de progresso em tempo real

🧠 Como funciona

Pipeline simplificado:

Upload da ISO original
Extração do conteúdo da ISO
Montagem do filesystem Linux (SquashFS)
Entrada em chroot para customização
Instalação de pacotes adicionais
Reconstrução do SquashFS
Recriação da ISO bootável

Resultado → uma ISO Linux customizada pronta para instalar ou rodar em VM

🛠️ Tecnologias usadas

Backend

Python 3
Flask
SquashFS tools
xorriso
rsync
chroot

Frontend

HTML5
CSS3
JavaScript (Fetch API)
📋 Requisitos

Sistema operacional: Linux

Dependências do sistema:

sudo apt install squashfs-tools xorriso rsync syslinux-utils

Dependências Python:

Python 3.10+
pip
⚙️ Instalação

Clone o repositório:

git clone https://github.com/joaovitor10br/SiteDesmontagemISO.git
cd SiteDesmontagemISO

Crie o ambiente virtual:

cd backend
python -m venv venv
source venv/bin/activate

Instale as dependências:

pip install -r requirements.txt
▶️ Executando o projeto
python app.py

Abra no navegador:

http://127.0.0.1:5000
🧪 Testado com
Debian Netinst ISO
VirtualBox
📦 Estrutura do projeto
backend/
 ├── app.py
 ├── iso_linux.py
 └── requirements.txt

frontend/
 ├── index.html
 ├── sobre.html
 └── style.css
⚠️ Observações importantes
O projeto precisa rodar com permissões de sudo para manipular ISOs.
Pode consumir bastante CPU/RAM durante a reconstrução da ISO.
Ideal usar SSD para melhor desempenho.
🎯 Objetivo do projeto

Este projeto foi desenvolvido com fins acadêmicos para demonstrar:

Manipulação de imagens Linux
Automação de sistemas
Integração backend + frontend
Processos de build de distribuições Linux
👨‍💻 Autor

João Vitor Alves Martins

📄 Licença

Este projeto é de uso acadêmico.
