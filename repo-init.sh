#! /bin/bash
user(){
 # Crear el grupo xbuilder y un usuario 'builder' asociado a él
          groupadd -f xbuilder
          useradd -m -s /bin/bash -G xbuilder builder
          
          # Permitir que el usuario builder use sudo sin contraseña para las tareas de xbps-src
          echo "builder ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/builder
       
}

clone(){
    echo "Clonando void-packages..."
    git clone --depth 1 https://github.com/void-linux/void-packages.git /tmp/void-packages
    cd /tmp/void-packages
    echo "XBPS_ALLOW_RESTRICTED=yes" >> etc/conf

    echo "Clonando repositorio oficial de plantillas neko-pkgs..."
    git clone --depth 1 https://github.com/Neko-Void-Linux/neko-pkgs.git /tmp/neko-pkgs
    cp -rf /tmp/neko-pkgs/* srcpkgs/
    rm -rf /tmp/neko-pkgs

    echo "#nnothing" >> common/build-style/none.sh
    cat << 'EOF' >> common/shlibs
libscenefx-0.5.so scenefx-latest-0.5_1
libQt6Pas.so.6 libqt6pas-bin-6.2.10_1
libcrypto.so.1.1 libssl1.1-1.1.1w_1
EOF

    # Cambiar la propiedad al usuario builder
    chown -R builder:builder /tmp/void-packages
}

build(){
    su - builder -c "cd /tmp/void-packages && ./xbps-src binary-bootstrap"
    if [ $# -gt 0 ]; then
        for pkg in "$@"; do
            echo "Compilando paquete: $pkg"
            su - builder -c "cd /tmp/void-packages && ./xbps-src pkg $pkg"
        done
    else
        DEFAULT_PKGS=(
            heroic-games protonup-qt steam-nk spicetify faugus-launcher
            portproton windscribe-desktop zerotierone brave-bin
            linux-neko-rolling linux-neko-rt linux-neko-zen waterfox
            vesktop kyoz sfwbar noctalia kitty-free kasha-installer
            linux-cachy-void zig-nk ly falcond-wd xwinwrap-nk mullvad-vpn
        )
        for pkg in "${DEFAULT_PKGS[@]}"; do
            echo "Compilando paquete: $pkg"
            su - builder -c "cd /tmp/void-packages && ./xbps-src pkg $pkg"
        done
    fi
}

ACTION="$1"
shift 2>/dev/null || true

case "$ACTION" in 
    user)
        echo "Configurando usuario builder..."
        user
        ;;
    build)
        echo "Iniciando compilación de paquetes..."
        build "$@"
        ;;
    clone)
        echo "Clonando y preparando entorno..."
        clone
        ;;
    *)
        echo "Uso: $0 {user|clone|build [paquetes...]}"
        exit 1
        ;;
esac
