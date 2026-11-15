#!/usr/bin/env python3
"""
sanitize_repodata.py - Blindaje contra corrupción de stage.plist y saneamiento de repodata en XBPS

Modos:
  --pre    : Ejecutado tras sembrar/descargar el repodata base. Si stage.plist existe de una corrida
             previa rota, promueve los paquetes a index.plist, purga shlibs de sistema no permitidas
             en paquetes de aplicación, y elimina stage.plist.
  --post   : Ejecutado tras xbps-rindex -a y antes de firmar. Si xbps-rindex desvió paquetes a
             stage.plist por Inconsistent shlibs, los promueve a index.plist, purga shlibs espurias
             y elimina stage.plist para que xbps-rindex --sign firme un índice 100% íntegro.
  --verify : Ejecutado en Control de Calidad Final. Verifica que stage.plist NO exista en el repodata
             y que los paquetes .xbps locales estén registrados en index.plist con la versión correcta.
"""

import argparse
import io
import os
import pathlib
import plistlib
import re
import sys
import tarfile
from typing import Dict, Optional, Set

FORBIDDEN_BASE_SHLIBS: Set[str] = {
    "libc.so.6",
    "ld-linux-x86-64.so.2",
    "ld-linux.so.2",
    "ld-linux-aarch64.so.1",
    "ld-linux-armhf.so.3",
    "ld-musl-x86_64.so.1",
    "ld-musl-aarch64.so.1",
    "ld-musl-armhf.so.1",
    "ld-musl-i386.so.1",
    "libdl.so.2",
    "libm.so.6",
    "libpthread.so.0",
    "libresolv.so.2",
    "librt.so.1",
    "libutil.so.1",
    "libnsl.so.1",
    "libanl.so.1",
    "libBrokenLocale.so.1",
    "libthread_db.so.1",
    "libmvec.so.1",
    "libc_malloc_debug.so.0",
    "libgcc_s.so.1",
    "libstdc++.so.6",
}

SYSTEM_CORE_PACKAGES: Set[str] = {
    "glibc",
    "glibc-32bit",
    "musl",
    "gcc-libs",
    "gcc-libs-32bit",
    "libgcc",
}


def read_repodata_members(repodata_path: pathlib.Path) -> Dict[str, bytes]:
    members = {}
    with tarfile.open(repodata_path, "r:*") as t:
        for m in t.getmembers():
            f = t.extractfile(m)
            if f:
                members[m.name] = f.read()
    return members


def write_repodata_members(repodata_path: pathlib.Path, members: Dict[str, bytes]) -> None:
    # Intenta zstd primero; si el entorno no soporta zstd en tarfile, usa gzip (totalmente compatible con xbps)
    write_modes = ["w:zst", "w:gz"]
    last_err: Optional[Exception] = None

    for mode in write_modes:
        try:
            with tarfile.open(repodata_path, mode) as t:
                for name, data in members.items():
                    ti = tarfile.TarInfo(name)
                    ti.size = len(data)
                    t.addfile(ti, io.BytesIO(data))
            return
        except (ValueError, tarfile.CompressionError) as e:
            last_err = e
            continue

    if last_err:
        raise last_err


def load_plist_safe(raw_bytes: bytes) -> dict:
    if not raw_bytes or not raw_bytes.strip():
        return {}
    try:
        data = plistlib.loads(raw_bytes)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def sanitize_pkg_entry(pkgname: str, entry: dict) -> bool:
    if pkgname in SYSTEM_CORE_PACKAGES:
        return False

    modified = False
    prov = entry.get("shlib-provides")
    if isinstance(prov, list):
        cleaned = [
            s for s in prov
            if s not in FORBIDDEN_BASE_SHLIBS
            and not s.startswith("libnss_")
            and not s.startswith("ld-linux")
            and not s.startswith("ld-musl")
        ]
        if len(cleaned) != len(prov):
            modified = True
            if cleaned:
                entry["shlib-provides"] = cleaned
            else:
                del entry["shlib-provides"]
    return modified


def process_repodata(binpkgs_dir: pathlib.Path, arch: str, mode: str) -> bool:
    repodata_file = binpkgs_dir / f"{arch}-repodata"
    if not repodata_file.is_file():
        print(f"[{mode.upper()}] Repodata {repodata_file.name} no encontrado en {binpkgs_dir}. Omitiendo.")
        return False

    members = read_repodata_members(repodata_file)
    if "index.plist" not in members:
        print(f"[{mode.upper()}] Archivo index.plist no encontrado dentro de {repodata_file.name}.")
        return False

    index_data = load_plist_safe(members["index.plist"])
    stage_data = load_plist_safe(members.get("stage.plist", b""))

    modified = False
    promoted_count = 0

    # 1. Promover paquetes atrapados en stage.plist hacia index.plist
    if stage_data:
        print(f"[{mode.upper()}] ¡Se detectaron {len(stage_data)} paquetes en stage.plist! Promoviendo a index.plist...")
        for pkg, entry in stage_data.items():
            pkgver = entry.get("pkgver", pkg)
            sanitize_pkg_entry(pkg, entry)
            index_data[pkg] = entry
            promoted_count += 1
            print(f"  -> Promovido a index.plist: {pkg} ({pkgver})")
        modified = True

    # 2. Purgar shlib-provides del sistema en paquetes de aplicación
    purged_pkgs = []
    for pkg, entry in index_data.items():
        if sanitize_pkg_entry(pkg, entry):
            purged_pkgs.append(pkg)
            modified = True

    if purged_pkgs:
        print(f"[{mode.upper()}] Shlibs del sistema purgadas en {len(purged_pkgs)} paquetes: {', '.join(purged_pkgs)}")

    if modified or "stage.plist" in members:
        new_members = {
            "index.plist": plistlib.dumps(index_data),
        }
        if "index-meta.plist" in members:
            new_members["index-meta.plist"] = members["index-meta.plist"]

        write_repodata_members(repodata_file, new_members)
        print(f"[{mode.upper()}] ✅ Repodata saneado con éxito: {len(index_data)} paquetes en index.plist, 0 en stage.plist.")

        marker = pathlib.Path("/tmp/.repodata-repaired")
        try:
            marker.touch()
        except Exception:
            pass

        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output and os.path.exists(github_output):
            try:
                with open(github_output, "a") as gh_out:
                    gh_out.write("repodata_repaired=true\n")
            except Exception:
                pass

        return True
    else:
        print(f"[{mode.upper()}] Repodata limpio: 0 en stage.plist, {len(index_data)} paquetes consistentes en index.plist.")
        return False


def verify_repodata(binpkgs_dir: pathlib.Path, arch: str) -> bool:
    repodata_file = binpkgs_dir / f"{arch}-repodata"
    if not repodata_file.is_file():
        print(f"[CONTROL DE CALIDAD] ❌ No existe el archivo de índice {repodata_file}")
        return False

    members = read_repodata_members(repodata_file)

    # 1. Verificar ausencia de paquetes atrapados en stage.plist
    if "stage.plist" in members:
        stage_data = load_plist_safe(members["stage.plist"])
        if stage_data:
            print(f"[CONTROL DE CALIDAD] ❌ ERROR CRÍTICO: stage.plist contiene {len(stage_data)} paquetes no publicados en index.plist:")
            for pkg, entry in stage_data.items():
                print(f"   - {pkg}: {entry.get('pkgver', 'desconocido')}")
            return False

    # 2. Verificar existencia de index.plist
    if "index.plist" not in members:
        print("[CONTROL DE CALIDAD] ❌ ERROR: index.plist no está presente en el repodata.")
        return False

    index_data = load_plist_safe(members["index.plist"])
    print(f"[CONTROL DE CALIDAD] Total de paquetes registrados en index.plist: {len(index_data)}")

    # 3. Mapear binarios locales en el directorio
    local_files = list(binpkgs_dir.glob(f"*.{arch}.xbps"))
    print(f"[CONTROL DE CALIDAD] Total de archivos .{arch}.xbps en el directorio: {len(local_files)}")

    pkg_to_local = {}
    for f in local_files:
        m = re.match(rf"^(.+)-([^-]+_[0-9]+)\.{re.escape(arch)}\.xbps$", f.name)
        if m:
            pname, pver = m.group(1), m.group(2)
            pkgver = f"{pname}-{pver}"
            if pname not in pkg_to_local:
                pkg_to_local[pname] = []
            pkg_to_local[pname].append((pkgver, f.name))

    discrepancies = []
    # Comprobar que los binarios locales estén representados en index.plist
    for pname, file_list in pkg_to_local.items():
        if pname not in index_data:
            discrepancies.append(f"Paquete local '{pname}' ({file_list[-1][1]}) no está registrado en index.plist")
        else:
            idx_pkgver = index_data[pname].get("pkgver")
            if not any(item[0] == idx_pkgver for item in file_list):
                discrepancies.append(
                    f"Versión de '{pname}' en index.plist ({idx_pkgver}) no coincide con los binarios locales ({[f[0] for f in file_list]})"
                )

    if discrepancies:
        print(f"[CONTROL DE CALIDAD] ❌ Se detectaron {len(discrepancies)} discrepancias entre los binarios y el índice:")
        for d in discrepancies:
            print(f"   ❌ {d}")
        return False

    print(f"[CONTROL DE CALIDAD] ✅ Validación exitosa: 0 paquetes en stage.plist, 100% de coherencia en index.plist ({len(index_data)} paquetes).")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="XBPS Repodata Sanitizer & Stage Promotor")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pre", action="store_true", help="Saneamiento pre-index y promoción de stage heredado")
    group.add_argument("--post", action="store_true", help="Promoción post-index de stage.plist hacia index.plist")
    group.add_argument("--verify", action="store_true", help="Validación estricta de index.plist y ausencia de stage.plist")

    parser.add_argument("--dir", default=".", help="Directorio que contiene el repodata y los .xbps (default: .)")
    parser.add_argument("--arch", default="x86_64", help="Arquitectura objetivo (default: x86_64)")

    args = parser.parse_args()
    binpkgs_dir = pathlib.Path(args.dir).resolve()

    if args.pre:
        process_repodata(binpkgs_dir, args.arch, "pre")
        sys.exit(0)
    elif args.post:
        process_repodata(binpkgs_dir, args.arch, "post")
        sys.exit(0)
    elif args.verify:
        success = verify_repodata(binpkgs_dir, args.arch)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
