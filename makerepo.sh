#!/bin/bash
# Script de compilación local para paquetes Neko en Void Linux
# Ejecutar como root o mediante sudo.

set -e

# ==========================================
# CONFIGURACIÓN LOCAL (Reemplaza estos datos)
# ==========================================
PRIVKEY_PATH="${PRIVKEY_PATH:-/ruta/a/tu/privkey.pem}"
SFTP_USER="${SFTP_USER:-tu_usuario}"
SFTP_PASS="${SFTP_PASS:-tu_contraseña}"
NEKO_PKGS_REPO="${NEKO_PKGS_REPO:-https://github.com/Neko-Void-Linux/neko-pkgs.git}"
# ==========================================

echo "Actualizando sistema e instalando dependencias..."
xbps-install -Syu xbps
xbps-install -Suyd
xbps-install -y nodejs ca-certificates ntp git base-devel xtools sudo rsync openssh sshpass

echo "Configurando usuario y grupo de compilación..."
groupadd -f xbuilder
if ! id "builder" &>/dev/null; then
    useradd -m -s /bin/bash -G xbuilder builder
fi
echo "builder ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/builder

echo "Preparando el entorno de void-packages en /tmp/void-packages..."
rm -rf /tmp/void-packages
git clone --depth 1 https://github.com/void-linux/void-packages.git /tmp/void-packages
cd /tmp/void-packages
echo "XBPS_ALLOW_RESTRICTED=yes" >> etc/conf

echo "Clonando repositorio oficial de plantillas neko-pkgs..."
rm -rf /tmp/neko-pkgs
git clone --depth 1 "$NEKO_PKGS_REPO" /tmp/neko-pkgs
cp -rfv /tmp/neko-pkgs/* srcpkgs/
rm -rf /tmp/neko-pkgs

echo "#nothing" >> common/build-style/none.sh
cat << 'EOF' >> common/shlibs
libscenefx-0.5.so scenefx-latest-0.5_1
libQt6Pas.so.6 libqt6pas-bin-6.2.10_1
libcrypto.so.1.1 libssl1.1-1.1.1w_1
EOF

chown -R builder:builder /tmp/void-packages

echo "Iniciando proceso de compilación con xbps-src..."
su - builder -c "cd /tmp/void-packages && ./xbps-src binary-bootstrap"

if [ $# -gt 0 ]; then
    for pkg in "$@"; do
        echo "Compilando paquete: $pkg"
        su - builder -c "cd /tmp/void-packages && ./xbps-src pkg $pkg"
    done
else
    PKGS=(
        heroic-games protonup-qt steam-nk spicetify faugus-launcher
        portproton windscribe-desktop zerotierone brave-bin
        linux-neko-rolling linux-neko-rt linux-neko-zen waterfox
        vesktop kyoz
    )
    for pkg in "${PKGS[@]}"; do
        echo "Compilando paquete: $pkg"
        su - builder -c "cd /tmp/void-packages && ./xbps-src pkg $pkg"
    done
fi

echo "Procesando paquetes y generando el índice del repositorio..."
cd /tmp/void-packages/hostdir/binpkgs

shopt -s nullglob
XBPS_FILES=(*.xbps)
if [ ${#XBPS_FILES[@]} -eq 0 ]; then
    echo "Aviso: No se encontraron archivos .xbps generados."
    exit 0
fi

if [ -f "$PRIVKEY_PATH" ]; then
    echo "Firmando paquetes con clave privada..."
    xbps-rindex --sign-pkg --privkey "$PRIVKEY_PATH" "${XBPS_FILES[@]}"
    xbps-rindex -a "${XBPS_FILES[@]}"
    xbps-rindex --sign --signedby "javierc" --privkey "$PRIVKEY_PATH" .
else
    echo "Aviso: Clave privada no encontrada en '$PRIVKEY_PATH'. Generando índice repodata sin firmar..."
    xbps-rindex -a "${XBPS_FILES[@]}"
fi

if [ "$SFTP_USER" != "tu_usuario" ] && [ -n "$SFTP_PASS" ] && [ "$SFTP_PASS" != "tu_contraseña" ]; then
    echo "Desplegando repositorio a SourceForge..."
    export SSHPASS="$SFTP_PASS"
    sshpass -e rsync -avz --delete -e "ssh -o StrictHostKeyChecking=no" . ${SFTP_USER}@frs.sourceforge.net:/home/frs/project/neko-void/repo/
else
    echo "Aviso: Credenciales SFTP no configuradas. Saltando despliegue a SourceForge."
fi

echo "Proceso completado con éxito. Los paquetes están disponibles localmente en /tmp/void-packages/hostdir/binpkgs/"
